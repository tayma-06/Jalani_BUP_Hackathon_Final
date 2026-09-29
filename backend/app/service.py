import asyncio
import copy
import json
import logging
import time
from collections import deque
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from app import metrics
from app.config import Settings
from app.db import (
    KEEP_HISTORY,
    Alert,
    Database,
    DemandObservation,
    Execution,
    Incident,
    Recommendation,
    SnapshotRecord,
    audit,
)
from app.intelligence.engine import (
    ACTIVE,
    POLICY,
    analyse,
    detect,
    historical_multiplier,
    recommend,
    validate_batch,
)
from app.sim.client import SimError, SimulatorClient
from app.sim.schemas import FUELS, Allocation, Instance, Snapshot

log = logging.getLogger("jalani")


class FuelService:
    def __init__(self, settings: Settings, client=None, db=None):
        self.config = settings
        self.client = client or SimulatorClient(settings)
        self.db = db or Database(settings.database_url)
        self.lock = asyncio.Lock()
        self.dirty = asyncio.Event()
        self.snapshot = None
        self.analysis = {}
        self.run_id = str(uuid4())
        self.fetched_at = 0.0
        self.stale = True
        self.mode = "DEGRADED"
        self.reason = "Waiting for a validated simulator snapshot"
        self.db_ok = False
        self.reset_notice = False
        self.generation_uncertain = False
        self.unresolved = False
        self.stream_connected = False
        self.history_gap = False
        self.corrupt_next = False
        self.restored = False
        self.autopilot = "advisory"
        self.policy = copy.deepcopy(POLICY)
        self.policy_name = "greedy_v1"
        self.recommendations = []
        self.alerts = []
        self.incidents = []
        self.decisions = []
        self.network_cache = {}
        self.latencies = deque(maxlen=1000)
        self.errors = deque(maxlen=1000)
        self.last_cycle_ms = None
        self.last_saved_tick = -100
        self.last_pruned_tick = -100
        # Demand history that has scrolled out of the simulator's per-request window. Seeded
        # from the database once per run, then kept in memory so a refresh does not re-read
        # and re-deserialise the whole window on every tick.
        self.history_cache = {}
        self.history_saved = set()
        self.history_run = None

    def event(self, name, **details):
        log.info(json.dumps({"event": name, "tick": self.snapshot.instance.tick if self.snapshot else None,
                             "run_id": self.run_id, **details}, default=str))

    @property
    def age(self):
        return time.monotonic() - self.fetched_at if self.fetched_at else None

    def safe(self):
        return (self.snapshot is not None and self.db_ok and not self.stale and not self.unresolved
                and not self.generation_uncertain and self.snapshot.consistent
                and self.age is not None and self.age <= self.config.max_state_age_seconds)

    def set_mode(self, mode, reason=""):
        if mode != self.mode:
            self.event("mode.changed", previous=self.mode, mode=mode, reason=reason)
        self.mode, self.reason = mode, reason

    def restore(self):
        self.db.check()
        self.db_ok = True
        if self.restored:
            return
        runtime = self.db.setting("runtime", {})
        self.run_id = runtime.get("run_id", self.run_id)
        # Scoped to this run: the newest snapshot overall may belong to a run that has since
        # been reset, which would silently leave the operator with no cached view.
        row = self.db.last_snapshot(self.run_id)
        if row and row.run_id == self.run_id:
            self.snapshot = Snapshot.model_validate(row.payload)
            self.analysis = analyse(self.snapshot, self.config.monte_carlo_paths, self.config.horizon_hours)
            self.build_network()
        self.autopilot = self.db.setting("autopilot", {"mode": "advisory"})["mode"]
        configured = self.db.setting("policy", {})
        self.policy_name = configured.get("version", "greedy_v1")
        self.policy.update({k: v for k, v in configured.items() if k in self.policy})
        self.restored = True

    async def refresh(self):
        async with self.lock:
            return await self._refresh()

    async def refresh_locked(self):
        """Refresh while the caller already holds `self.lock`.

        `asyncio.Lock` is not reentrant, so an HTTP handler that must refresh inside an
        existing critical section uses this rather than `refresh()`, which would deadlock.
        """
        return await self._refresh()

    async def _refresh(self):
        start_time = time.monotonic()
        try:
            self.restore()
            since = self.snapshot.instance.tick if self.snapshot else 0
            snapshot, stale = await self.client.snapshot(since)
            if self.corrupt_next:
                self.corrupt_next = False
                raise ValueError("Demo: invalid simulator response rejected")
            if stale:
                raise ValueError("Simulator marked its REST data as stale")
            if not snapshot.consistent:
                raise ValueError("Simulator moved too far during snapshot reads; retrying")
            previous = self.snapshot
            reset = self.reset_notice
            if previous:
                old, new = previous.instance, snapshot.instance
                def identity(i):
                    return i.id, i.scenario_id, i.scenario_version, i.seed
                old_keys = {a.idempotency_key for a in previous.allocations}
                new_keys = {a.idempotency_key for a in snapshot.allocations}
                reset |= (new.tick < old.tick or new.sim_time < old.sim_time or identity(old) != identity(new)
                          or bool(old_keys - new_keys)
                          or snapshot.metrics.served_demand_liters + 0.01 < previous.metrics.served_demand_liters)
            if reset:
                self.run_id = str(uuid4())
                self.analysis = {}
                self.last_saved_tick = -100
                self.last_pruned_tick = -100
                self.generation_uncertain = False
                self.history_cache, self.history_saved, self.history_run = {}, set(), None
                self.event("sim.reset_detected")
            self.reset_notice = False
            # Merge persisted history into the current run; IDs are only unique within a run.
            with self.db.session() as session:
                if self.history_run != self.run_id:
                    self.history_cache = {
                        row.sim_id: row.payload
                        for row in session.scalars(
                            select(DemandObservation)
                            .where(DemandObservation.run_id == self.run_id)
                            .order_by(DemandObservation.tick.desc())
                            .limit(KEEP_HISTORY)
                        )
                    }
                    self.history_saved = set(self.history_cache)
                    self.history_run = self.run_id
                station_map = {s.id: s for s in snapshot.stations}
                for observation in snapshot.history:
                    station = station_map[observation.station_id]
                    payload = observation.model_dump(mode="json")
                    # Recomputed for the visible window every tick, because a demand event
                    # starting or ending changes what counts as unexplained demand.
                    payload["demand_multiplier"] = historical_multiplier(snapshot, station, observation.tick)
                    self.history_cache[observation.id] = payload
                    if observation.id not in self.history_saved:
                        session.add(DemandObservation(run_id=self.run_id, sim_id=observation.id,
                            tick=observation.tick, station_id=observation.station_id, fuel_type=observation.fuel_type,
                            demand=observation.demand_liters, payload=payload))
                        self.history_saved.add(observation.id)
                if len(self.history_cache) > KEEP_HISTORY:
                    self.trim_history()
                if self.history_cache:
                    snapshot = Snapshot.model_validate(
                        {**snapshot.model_dump(mode="json"), "history": list(self.history_cache.values())})
                expected = max(0, snapshot.start_tick - max(since if not reset else 0, 0)) * len(snapshot.stations) * 3
                recent = sum(since < h.tick <= snapshot.start_tick for h in snapshot.history) if not reset else 0
                self.history_gap = expected > recent
                ledger = {a.idempotency_key: a for a in snapshot.allocations}
                unresolved = False
                for execution in self.db.open_executions(session, self.run_id):
                    if execution.run_id != self.run_id:
                        if execution.status in {"UNKNOWN", "PREPARED", "PENDING", "IN_TRANSIT"}:
                            execution.status = "ABANDONED_RESET"
                            execution.failure_reason = "Simulator run changed; old requests must never be replayed"
                        continue
                    match = ledger.get(execution.idempotency_key)
                    if match:
                        actual = {key: getattr(match, key) for key in execution.body}
                        if actual != execution.body:
                            self.generation_uncertain = True
                            execution.failure_reason = "Idempotency ledger body mismatch; operator investigation required"
                            unresolved = True
                            continue
                        execution.status, execution.sim_id = match.status, match.id
                        execution.failure_reason = match.failure_reason
                    elif execution.status in {"PREPARED", "UNKNOWN"}:
                        execution.status = "UNKNOWN"
                        unresolved = True
                self.unresolved = unresolved
                self.db.put_setting(session, "runtime", {"run_id": self.run_id, "tick": snapshot.instance.tick})
                # Saving a snapshot is the only thing that can push the snapshot table past its
                # cap, so pruning always follows a write and additionally runs on its own cadence.
                saved = snapshot.instance.tick - self.last_saved_tick >= 4 or reset or not previous
                if saved:
                    session.add(SnapshotRecord(run_id=self.run_id, tick=snapshot.instance.tick,
                                               payload=snapshot.model_dump(mode="json")))
                    self.last_saved_tick = snapshot.instance.tick
                if saved or snapshot.instance.tick - self.last_pruned_tick >= 8 or reset or not previous:
                    self.db.prune(session, self.run_id)
                    self.last_pruned_tick = snapshot.instance.tick
                session.commit()
            analysis = await asyncio.to_thread(analyse, snapshot, self.config.monte_carlo_paths, self.config.horizon_hours)
            new_alerts = detect(snapshot, analysis, None if reset else previous)
            if self.history_gap:
                new_alerts.append({"key": "history_gap", "type": "history_gap", "entity_id": "simulator",
                                   "severity": "WARNING", "message": "Demand rows are missing; forecast history is incomplete.",
                                   "last_tick": snapshot.instance.tick, "event_ids": []})
            plans = await asyncio.to_thread(recommend, snapshot, analysis, self.policy,
                                            self.config.monte_carlo_paths, self.policy_name)
            self.persist_intelligence(snapshot, new_alerts, plans)
            self.snapshot, self.analysis = snapshot, analysis
            self.fetched_at = time.monotonic()
            self.stale, self.db_ok = False, True
            self.load_views()
            if self.unresolved or self.generation_uncertain:
                self.set_mode("RECOVERING", "An uncertain shipment requires ledger reconciliation before further allocations")
            else:
                if self.mode != "NORMAL":
                    self.event("recovery.completed")
                self.set_mode("NORMAL")
            self.build_network()
            self.update_metrics()
            return True
        except SQLAlchemyError:
            self.db_ok = False
            self.stale = True
            self.set_mode("DEGRADED", "Database unavailable; cached reads only and allocation execution blocked")
            self.event("integration.error", component="database")
        except (SimError, ValueError, KeyError, TypeError) as exc:
            self.stale = True
            self.set_mode("DEGRADED", str(exc)[:240])
            self.event("integration.error", component="simulator", error=str(exc)[:240])
        finally:
            self.last_cycle_ms = round((time.monotonic() - start_time) * 1000, 2)
            metrics.cycle.observe((time.monotonic() - start_time))
            metrics.sim_up.set(0 if self.stale else 1)
            metrics.stale.set(int(not self.safe()))
        return False

    def trim_history(self):
        """Drop the oldest observations, keeping the same window every read would return."""
        excess = len(self.history_cache) - KEEP_HISTORY
        if excess <= 0:
            return
        oldest = sorted(self.history_cache, key=lambda key: self.history_cache[key]["tick"])[:excess]
        for key in oldest:
            del self.history_cache[key]
            self.history_saved.discard(key)

    def persist_intelligence(self, snapshot, alerts, plans):
        tick = snapshot.instance.tick
        with self.db.session() as session:
            open_recs = list(session.scalars(select(Recommendation).where(Recommendation.status == "PROPOSED")))
            valid_routes = {r.id for r in snapshot.routes if r.status == "AVAILABLE"}
            for row in open_recs:
                if row.run_id != self.run_id or tick - row.created_tick > 4 or row.payload["action"]["route_id"] not in valid_routes:
                    row.status = "EXPIRED"
            # Expiring the backlog above can overshoot the retention cap, so re-apply it here.
            self.db.prune(session, self.run_id)
            existing = {(r.payload["station_id"], r.payload["fuel_type"]) for r in open_recs if r.status == "PROPOSED"}
            for plan in plans:
                if (plan["station_id"], plan["fuel_type"]) not in existing:
                    row = Recommendation(run_id=self.run_id, created_tick=tick, status="PROPOSED", payload=plan)
                    session.add(row)
                    metrics.recommendations.labels("PROPOSED").inc()
                    metrics.confidence.observe(plan["confidence"]["score"])
                    if plan["requires_review"]:
                        metrics.review.inc()
                    self.event("decision.proposed", station_id=plan["station_id"], fuel_type=plan["fuel_type"])
            seen = set()
            for item in alerts:
                key = f"{self.run_id}:{item['key']}"
                seen.add(key)
                row = session.get(Alert, key)
                if row:
                    item["first_tick"] = row.payload["first_tick"]
                    row.payload = item
                    if row.status == "RESOLVED":
                        row.status = "OPEN"
                else:
                    item["first_tick"] = tick
                    session.add(Alert(id=key, run_id=self.run_id, status="OPEN", payload=item))
                    session.add(Incident(id=key, run_id=self.run_id, payload={"tick": tick,
                        "text": item["message"] + " Review affected stations and available allocation recommendations.",
                        "source": "template", "severity": item["severity"]}))
                    metrics.alerts.labels(item["severity"]).inc()
            for row in session.scalars(select(Alert).where(Alert.run_id == self.run_id)):
                # Prometheus owns its own firing/resolved lifecycle. Older installations
                # may have stored these rows under the current simulator run.
                if row.payload.get("source") == "prometheus":
                    continue
                if row.id not in seen and tick - row.payload["last_tick"] >= 4:
                    row.status = "RESOLVED"
            # Alerts and incidents are written after the prune above, so re-apply the caps to
            # keep a long-lived run from exceeding them by one cycle's worth of rows.
            self.db.prune(session, self.run_id)
            session.commit()

    def load_views(self):
        with self.db.session() as session:
            self.recommendations = [{**r.payload, "id": r.id, "run_id": r.run_id, "status": r.status}
                for r in session.scalars(select(Recommendation).where(Recommendation.run_id == self.run_id)
                                        .order_by(Recommendation.created_tick.desc()).limit(200))]
            self.alerts = []
            for row in session.scalars(
                    select(Alert).where(or_(Alert.run_id == self.run_id, Alert.run_id == "monitoring"))):
                view = {**row.payload, "id": row.id, "status": row.status}
                # Every alert exposes the same label set, so callers never branch on the
                # original source: Prometheus alerts carry labels already, simulator alerts
                # fall back to the fields the legacy Alerts page rendered.
                view["labels"] = row.payload.get("labels") or {
                    "alertname": row.payload.get("type") or row.payload.get("alertname") or "UnknownAlert",
                    "severity": row.payload.get("severity", "none")}
                self.alerts.append(view)
            self.incidents = [{"id": r.id, **r.payload} for r in session.scalars(
                select(Incident).where(Incident.run_id == self.run_id))][-50:][::-1]
            self.decisions = [self.execution_json(r) for r in session.scalars(
                select(Execution).order_by(Execution.created_at.desc()).limit(500))]

    @staticmethod
    def execution_json(row):
        return {"id": row.id, "recommendation_id": row.recommendation_id, "run_id": row.run_id,
                "status": row.status, "actor": row.actor, "sim_id": row.sim_id, "request": row.body,
                "failure_reason": row.failure_reason, "created_at": row.created_at}

    def build_network(self):
        if not self.snapshot:
            return
        s = self.snapshot
        stations, depots, regions = [], [], []
        for station in s.stations:
            fuels = {}
            for fuel in FUELS:
                a = self.analysis.get((station.id, fuel), {})
                fuels[fuel] = {"inventory": station.inventory[fuel], "capacity": station.capacity[fuel],
                               "in_transit": sum(q for _, q in a.get("inbound", [])),
                               **{k: a.get(k) for k in ("forecast_12h", "hours_to_stockout", "risk", "risk_level")}}
            stations.append({**station.model_dump(mode="json"), "fuels": fuels})
        for depot in s.depots:
            fuels = {}
            for fuel in FUELS:
                supply = [x.planned_tick for x in s.supply if x.depot_id == depot.id and x.fuel_type == fuel and x.status != "ARRIVED"]
                fuels[fuel] = {"inventory": depot.inventory[fuel], "capacity": depot.capacity[fuel],
                               "next_supply_tick": min(supply) if supply else None}
            used = sum(a.quantity for a in s.allocations if a.source_depot_id == depot.id and a.status in ACTIVE)
            depots.append({**depot.model_dump(mode="json"), "dispatch_used": used, "fuels": fuels})
        for region in s.regions:
            ids = {st.id for st in s.stations if st.region_id == region.id}
            fuels = {}
            hour_ticks = max(1, int(60 / s.instance.tick_minutes))
            for fuel in FUELS:
                rows = [h for h in s.history if h.station_id in ids and h.fuel_type == fuel
                        and h.tick > s.instance.tick - hour_ticks]
                fuels[fuel] = {"demand_last_hour": sum(r.demand_liters for r in rows),
                               "normal_last_hour": sum(r.demand_liters / r.demand_multiplier for r in rows if r.demand_multiplier),
                               "forecast_12h": sum(self.analysis[(sid, fuel)]["forecast_12h"] for sid in ids)}
            regions.append({**region.model_dump(mode="json"), "fuels": fuels})
        self.network_cache = {"run_id": self.run_id, "tick": s.instance.tick, "sim_time": s.instance.sim_time.isoformat(),
            "sim_status": s.instance.status, "tick_minutes": s.instance.tick_minutes,
            "snapshot_start_tick": s.start_tick, "snapshot_end_tick": s.end_tick,
            "tick_span": s.end_tick - s.start_tick, "history_gap": self.history_gap,
            "kpis": {"service_level": s.metrics.service_level, "unmet_liters": s.metrics.unmet_demand_liters,
                     "in_transit_liters": sum(a.quantity for a in s.allocations if a.status in ACTIVE),
                     "open_alerts": sum(a["status"] != "RESOLVED" for a in self.alerts),
                     "pending_recommendations": sum(r["status"] == "PROPOSED" for r in self.recommendations)},
            "stations": stations, "depots": depots, "regions": regions,
            "routes": [r.model_dump(mode="json") for r in s.routes],
            "autopilot": self.autopilot, "policy_version": self.policy_name}

    def network(self):
        too_old = self.age is None or self.age > self.config.max_state_age_seconds
        return {"stations": [], "depots": [], "routes": [], "regions": [], "kpis": {}, **self.network_cache,
                "mode": "DEGRADED" if too_old else self.mode, "stale": self.stale or too_old,
                "data_age_s": round(self.age, 2) if self.age is not None else None,
                "execution_blocked": not self.safe(), "reason": self.reason or ("Snapshot expired" if too_old else ""),
                "stream": "connected" if self.stream_connected else "polling"}

    def update_metrics(self):
        s = self.snapshot
        metrics.age.set(self.age or 0)
        metrics.mode.set({"NORMAL": 0, "DEGRADED": 1, "RECOVERING": 2}[self.mode])
        for station in s.stations:
            for fuel in FUELS:
                a = self.analysis[(station.id, fuel)]
                metrics.inventory.labels(station.id, fuel).set(station.inventory[fuel])
                metrics.risk.labels(station.id, fuel).set(a["risk"])
                metrics.cover.labels(station.id, fuel).set(a["hours_to_stockout"] if a["hours_to_stockout"] is not None else -1)
        for depot in s.depots:
            for fuel in FUELS:
                metrics.depot_inventory.labels(depot.id, fuel).set(depot.inventory[fuel])
        metrics.service.set(s.metrics.service_level)
        metrics.unmet.set(s.metrics.unmet_demand_liters)
        metrics.failures.set(s.metrics.allocation_failures)

    async def final_gate(self, allow_unresolved=False):
        self.db.check()
        if allow_unresolved:
            permitted = (self.snapshot and self.db_ok and not self.stale and not self.generation_uncertain
                         and self.age is not None and self.age <= self.config.max_state_age_seconds)
        else:
            permitted = self.safe()
        if not permitted:
            raise HTTPException(503, self.reason or "Execution requires fresh state and resolved shipments")
        raw, stale = await self.client.request("GET", "/v1/instance")
        instance = Instance.model_validate(raw)
        if stale or instance.tick < self.snapshot.instance.tick or instance.tick - self.snapshot.start_tick > self.config.max_tick_lag:
            raise HTTPException(409, "Simulator tick moved beyond the execution gate; refresh and review again")

    async def approve(self, rec_id, actor, quantity=None):
        async with self.lock:
            try:
                self.db.check()
                with self.db.session() as session:
                    existing = session.scalar(select(Execution).where(Execution.recommendation_id == rec_id))
                    if existing:
                        if quantity is not None and quantity != existing.body["quantity"]:
                            raise HTTPException(409, "An immutable shipment already exists with a different quantity")
                        return self.execution_json(existing)
                await self._refresh()
                await self.final_gate()
                with self.db.session() as session:
                    rec = session.get(Recommendation, rec_id, with_for_update=True)
                    if not rec or rec.run_id != self.run_id:
                        raise HTTPException(404, "Recommendation not found in the current run")
                    if rec.status != "PROPOSED":
                        raise HTTPException(409, f"Recommendation is {rec.status.lower()}")
                    if actor == "AUTOPILOT" and (self.autopilot != "auto" or rec.payload["requires_review"]
                                                 or rec.payload["confidence"]["score"] < 0.75):
                        raise HTTPException(409, "Human review is required")
                    action = rec.payload["action"]
                    qty = quantity if quantity is not None else action["quantity"]
                    if qty > action["quantity"]:
                        raise HTTPException(422, "Modified quantity may only reduce the reviewed proposal")
                    body = {"idempotency_key": f"jal-{self.run_id}-{rec_id}",
                            "source_depot_id": action["source_depot_id"], "route_id": action["route_id"],
                            "destination_station_id": rec.payload["station_id"],
                            "fuel_type": rec.payload["fuel_type"], "quantity": qty}
                    violation = validate_batch(self.snapshot, [body], self.policy)
                    if violation:
                        raise HTTPException(409, violation)
                    execution = Execution(recommendation_id=rec.id, run_id=self.run_id,
                        idempotency_key=body["idempotency_key"], body=body, actor=actor, status="PREPARED")
                    session.add(execution)
                    rec.status = "APPROVED"
                    audit(session, actor, "decision.approved", rec.id, body=body, run_id=self.run_id)
                    session.commit()  # No simulator POST may precede this durable intent.
                    execution_id = execution.id
                self.event("decision.approved", recommendation_id=rec_id, actor=actor)
                return await self.send_intent(execution_id)
            except SQLAlchemyError as exc:
                self.db_ok = False
                self.set_mode("DEGRADED", "Database commit failed; execution stopped")
                raise HTTPException(503, self.reason) from exc

    async def send_intent(self, execution_id):
        with self.db.session() as session:
            execution = session.get(Execution, execution_id)
            body = copy.deepcopy(execution.body)
        self.unresolved = True
        try:
            raw, _ = await self.client.request("POST", "/v1/allocations", body)
            allocation = Allocation.model_validate(raw)
            if any(getattr(allocation, key) != value for key, value in body.items()):
                raise ValueError("Simulator accepted an unexpected allocation body")
            status, sim_id, failure = allocation.status, allocation.id, allocation.failure_reason
        except (SimError, ValueError) as exc:
            status = "FAILED" if isinstance(exc, SimError) and 400 <= exc.status < 500 else "UNKNOWN"
            sim_id, failure = None, str(exc)[:500]
        with self.db.session() as session:
            execution = session.get(Execution, execution_id)
            execution.status, execution.sim_id, execution.failure_reason = status, sim_id, failure
            audit(session, execution.actor, "decision.executed" if sim_id is not None else "decision.failed",
                  execution.recommendation_id, status=status, sim_id=sim_id, reason=failure)
            session.commit()
            result = self.execution_json(execution)
        self.event("decision.executed" if sim_id is not None else "decision.failed", execution_id=execution_id, status=status)
        if sim_id is not None:
            metrics.executions.labels("auto" if execution.actor == "AUTOPILOT" else "manual").inc()
        await self._refresh()
        return result

    async def retry(self, execution_id, actor):
        async with self.lock:
            await self._refresh()
            await self.final_gate(allow_unresolved=True)
            with self.db.session() as session:
                execution = session.get(Execution, execution_id)
                if not execution or execution.run_id != self.run_id:
                    raise HTTPException(404, "Shipment intent not found in this run")
                if execution.status not in {"PREPARED", "UNKNOWN"}:
                    return self.execution_json(execution)
                violation = validate_batch(self.snapshot, [execution.body], self.policy)
                if violation:
                    raise HTTPException(409, f"Keep original intent pending: {violation}")
                audit(session, actor, "decision.retry", execution_id, idempotency_key=execution.idempotency_key)
                session.commit()
            return await self.send_intent(execution_id)

    async def run(self):
        while True:
            self.dirty.clear()
            try:
                await self.refresh()
                if self.autopilot == "auto" and self.safe():
                    for rec in list(self.recommendations):
                        if rec["status"] == "PROPOSED" and not rec["requires_review"]:
                            try:
                                await self.approve(rec["id"], "AUTOPILOT")
                            except (HTTPException, SimError):
                                break
            except Exception as exc:
                self.stale = True
                self.set_mode("DEGRADED", "Refresh failed; see service logs")
                self.event("integration.error", error=type(exc).__name__)
            try:
                await asyncio.wait_for(self.dirty.wait(), timeout=self.config.poll_seconds)
                # Coalesce rapid SSE ticks; never run concurrent refreshes.
                await asyncio.sleep(min(0.2, self.config.poll_seconds))
            except TimeoutError:
                pass

    async def stream(self):
        delay = 1
        while True:
            try:
                async with self.client.http.stream("GET", "/v1/stream", timeout=20) as response:
                    response.raise_for_status()
                    self.stream_connected = True
                    metrics.stream.set(1)
                    self.dirty.set()  # No replay exists: every reconnect needs a full REST refresh.
                    delay = 1
                    event_name = ""
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                        if line.startswith("data:"):
                            if event_name == "simulator.notice" and "reset" in line.lower():
                                self.reset_notice = True
                            self.dirty.set()
            except Exception as exc:
                self.event("integration.error", component="stream", error=type(exc).__name__)
            finally:
                self.stream_connected = False
                metrics.stream.set(0)
                self.dirty.set()
            await asyncio.sleep(delay)
            delay = min(20, delay * 2)

    def health(self):
        try:
            self.db.check()
            self.db_ok = True
        except SQLAlchemyError:
            self.db_ok = False
        current = self.network()
        times = sorted(self.latencies)
        p95 = times[min(len(times) - 1, int(len(times) * 0.95))] if times else None
        return {"status": "healthy" if self.safe() else "degraded", "mode": current["mode"],
                "version": self.config.app_version, "git_sha": self.config.git_sha,
                "p95_latency_ms": round(p95 * 1000, 2) if p95 is not None else None,
                "error_rate": sum(self.errors) / len(self.errors) if self.errors else None,
                "reason": self.reason, "components": {
                    "backend": {"status": "healthy"}, "database": {"status": "healthy" if self.db_ok else "down"},
                    "simulator": {"status": "healthy" if not current["stale"] else "unhealthy",
                                  "data_age_s": current["data_age_s"],
                                  "circuit": "open" if self.client.open_until > time.monotonic() else "closed"},
                    "prediction_service": {"status": "healthy", "model": "profile_v1", "kind": "local statistical forecast"},
                    "decision_engine": {"status": "healthy" if self.safe() else "blocked", "policy": self.policy_name,
                                        "last_cycle_ms": self.last_cycle_ms},
                    "event_stream": {"status": "connected" if self.stream_connected else "polling"},
                    "llm": {"status": "disabled", "explanations": "grounded templates"}}}
