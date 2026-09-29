"""Hour-aware forecasts, reproducible uncertainty and constrained decisions."""
import hashlib
from datetime import timedelta
from pathlib import Path

import numpy as np
import yaml

from app.sim.schemas import FUELS, AllocationBody, Snapshot

CONFIG = Path(__file__).resolve().parents[2] / "config"
PROFILES = yaml.safe_load((CONFIG / "profiles.yaml").read_text())
POLICY = yaml.safe_load((CONFIG / "policy.yaml").read_text())
ACTIVE = {"PENDING", "IN_TRANSIT"}


def inbound(snapshot, station_id, fuel):
    routes = {r.id: r for r in snapshot.routes}
    result = []
    for allocation in snapshot.allocations:
        if (allocation.destination_station_id == station_id and allocation.fuel_type == fuel
                and allocation.status in ACTIVE):
            eta = allocation.expected_arrival_tick
            if eta is None:
                departure = max(snapshot.instance.tick + 1, allocation.created_tick + 1)
                eta = departure + routes[allocation.route_id].transit_ticks
            result.append((max(snapshot.instance.tick + 1, eta), allocation.quantity))
    return result


def event_applies(event, station=None, depot=None, route=None, region=None):
    p = event.parameters
    checks = []
    if station:
        checks += [("station_ids", station.id), ("region_ids", station.region_id)]
    if depot:
        checks += [("depot_ids", depot.id)]
    if route:
        checks += [("route_ids", route.id)]
    if region:
        checks += [("region_ids", region.id)]
    return all(not p.get(key) or value in p[key] for key, value in checks)


def scoped_event_ids(snapshot, station=None, depot=None, route=None, region=None):
    """Only the ACTIVE events that actually reach this entity, following the network.

    An alert must never claim an event that cannot reach its station, region, route or
    depot, otherwise every crisis in the world appears to explain every alert. A route
    disruption also reaches the depot that owns the route, and a station-scoped event
    reaches that station's region, so the identifiers are resolved through the topology.
    """
    if route is not None and depot is None:
        depot = next((d for d in snapshot.depots if d.id == route.source_depot_id), None)
    station_ids = [station.id] if station is not None else []
    region_ids = ([station.region_id] if station is not None else []) + ([region.id] if region is not None else [])
    depot_ids = [depot.id] if depot is not None else []
    # A route alert is about that route only. A depot alert also covers every route it owns.
    route_ids = [route.id] if route is not None else (
        [r.id for r in snapshot.routes if r.source_depot_id == depot.id] if depot is not None else [])
    result = []
    for event in snapshot.events:
        if event.status != "ACTIVE":
            continue
        p = event.parameters
        if any(wanted and ids and not set(wanted) & set(ids)
               for wanted, ids in ((p.get("station_ids"), station_ids),
                                    (p.get("depot_ids"), depot_ids),
                                    (p.get("route_ids"), route_ids),
                                    (p.get("region_ids"), region_ids))):
            continue
        result.append(event.id)
    return result


def historical_multiplier(snapshot, station, tick):
    """Reconstruct known demand events; unexplained multipliers remain an assumption."""
    current_events = 1.0
    historical_events = 1.0
    for event in snapshot.events:
        if event.type == "demand_spike" and event_applies(event, station=station):
            multiplier = max(0.01, event.parameters.get("multiplier", 1.5))
            if event.start_tick <= snapshot.instance.tick < event.end_tick:
                current_events *= multiplier
            if event.start_tick <= tick < event.end_tick:
                historical_events *= multiplier
    return station.demand_multiplier / current_events * historical_events


def normalized_history(snapshot, station, fuel):
    """(row, baseline litres/hour) observations in chronological order, event-normalised.

    Baseline demand divides reported litres by the demand event multiplier in force at
    that tick, so a spike leaves a comparable hour-of-day baseline across history.
    """
    tick_minutes = snapshot.instance.tick_minutes
    history = sorted((h for h in snapshot.history if h.station_id == station.id and h.fuel_type == fuel),
                     key=lambda h: h.tick)
    history = history[-int(14 * 24 * 60 / tick_minutes):]
    observations = []
    for row in history:
        multiplier = row.demand_multiplier
        if multiplier is None:
            multiplier = historical_multiplier(snapshot, station, row.tick)
        if multiplier > 0:
            observations.append((row, row.demand_liters / multiplier))
    return observations


def forecast(snapshot, station, fuel, hours=24, forecaster=None):
    tick_minutes = snapshot.instance.tick_minutes
    count = max(1, int(hours * 60 / tick_minutes))
    if forecaster is not None:
        # A trained model replaces the statistical profile when it covers this pair.
        baseline = forecaster.baseline(snapshot, station, fuel, count)
        if baseline is not None:
            mean, std = zip(*baseline) if baseline else ((), ())
            observations = normalized_history(snapshot, station, fuel)
            return {"mean": [round(float(m), 2) for m in mean],
                    "std": [round(float(s), 2) for s in std],
                    "model_version": f"ml-{forecaster.version}",
                    "source": f"trained ridge forecast {forecaster.version}",
                    "known": True, "history_rows": len(observations),
                    "history": [{"tick": row.tick, "litres_per_hour": round(value, 2)}
                                for row, value in observations[-96:]]}
    profile = PROFILES.get(station.demand_profile)
    region = next(r for r in snapshot.regions if r.id == station.region_id)
    history = sorted((h for h in snapshot.history if h.station_id == station.id and h.fuel_type == fuel),
                     key=lambda h: h.tick)
    history = history[-int(14 * 24 * 60 / tick_minutes):]
    observations = []
    for row in history:
        multiplier = row.demand_multiplier
        if multiplier is None:
            multiplier = historical_multiplier(snapshot, station, row.tick)
        if multiplier > 0:
            observations.append((row, row.demand_liters / multiplier))

    def prior(hour):
        if profile:
            factor = profile["busy_factor"] if hour in profile["busy_hours"] else profile["quiet_factor"]
            return profile["daily"][fuel] * tick_minutes / 1440 * factor * region.demand_factor
        return float(np.mean([value for _, value in observations[-16:]])) if observations else 0.0

    learned = {}
    for hour in range(24):
        values = [value for row, value in observations if row.sim_time.hour == hour]
        if len(values) >= max(2, int(60 / tick_minutes)):
            learned[hour] = float(np.mean(values))
    correction = 1.0
    errors = []
    for row, value in observations[-8:]:
        base = learned.get(row.sim_time.hour, prior(row.sim_time.hour))
        if base > 0:
            correction = 0.7 * correction + 0.3 * float(np.clip(value / base, 0.5, 2))
            errors.append(value - base)
    means, stds = [], []
    for step in range(1, count + 1):
        hour = (snapshot.instance.sim_time + timedelta(minutes=tick_minutes * step)).hour
        mean = max(0, learned.get(hour, prior(hour)) * station.demand_multiplier * correction)
        noise = profile["noise"] if profile else 0.25
        residual = float(np.std(errors)) * station.demand_multiplier if len(errors) >= 4 else 0.0
        means.append(mean)
        stds.append(max(mean * noise, residual))
    return {"mean": means, "std": stds, "model_version": "profile_v1",
            "source": "learned hourly profile" if len(learned) == 24 else "documented prior + observed correction",
            "known": bool(profile or observations), "history_rows": len(history),
            "history": [h.model_dump(mode="json") for h in history[-96:]]}


def seed_for(snapshot, station_id, fuel):
    key = f"{snapshot.instance.seed}:{snapshot.instance.tick}:{station_id}:{fuel}"
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big")


def project(inventory, capacity, means, stds, arrivals, tick, tick_minutes, seed, paths=300):
    """Arrivals precede demand; common random numbers compare counterfactuals fairly."""
    rng = np.random.default_rng(seed)
    demand = np.maximum(0, rng.normal(np.array(means), np.array(stds), size=(paths, len(means))))
    stocks = np.full(paths, float(inventory))
    unmet = np.zeros(paths)
    deterministic_stock = float(inventory)
    first_shortage = None
    inventory_path = []
    arrivals_by_tick = {}
    for eta, quantity in arrivals:
        arrivals_by_tick[eta] = arrivals_by_tick.get(eta, 0) + quantity
    for i, mean in enumerate(means):
        arriving = arrivals_by_tick.get(tick + i + 1, 0)
        stocks = np.minimum(capacity, stocks + arriving)
        unmet += np.maximum(0, demand[:, i] - stocks)
        stocks = np.maximum(0, stocks - demand[:, i])
        deterministic_stock = min(capacity, deterministic_stock + arriving)
        if mean > deterministic_stock + 1e-9 and first_shortage is None:
            first_shortage = (i + 1) * tick_minutes / 60
        deterministic_stock = max(0, deterministic_stock - mean)
        inventory_path.append(round(deterministic_stock, 2))
    risk = float(np.mean(unmet > 1e-6))
    return {"risk": risk, "expected_unmet_liters": round(float(unmet.mean()), 2),
            "hours_to_stockout": first_shortage, "inventory_path": inventory_path,
            "risk_level": "CRITICAL" if risk > 0.8 else "HIGH" if risk >= 0.5 else "MEDIUM" if risk >= 0.2 else "LOW"}


def analyse(snapshot: Snapshot, paths=300, horizon_hours=12, forecaster=None):
    result = {}
    horizon = max(1, int(horizon_hours * 60 / snapshot.instance.tick_minutes))
    for station in snapshot.stations:
        for fuel in FUELS:
            prediction = forecast(snapshot, station, fuel, hours=max(24, horizon_hours), forecaster=forecaster)
            scheduled = inbound(snapshot, station.id, fuel)
            risk = project(station.inventory[fuel], station.capacity[fuel], prediction["mean"][:horizon],
                           prediction["std"][:horizon], scheduled, snapshot.instance.tick,
                           snapshot.instance.tick_minutes, seed_for(snapshot, station.id, fuel), paths)
            available = [r.transit_ticks + 1 for r in snapshot.routes
                         if r.destination_station_id == station.id and r.status == "AVAILABLE"]
            if risk["hours_to_stockout"] is not None and available:
                if risk["hours_to_stockout"] <= min(available) * snapshot.instance.tick_minutes / 60:
                    risk["risk_level"] = "CRITICAL"
            result[(station.id, fuel)] = {**prediction, **risk, "inbound": scheduled,
                                         "forecast_12h": sum(prediction["mean"][:horizon])}
    return result


def budgets(snapshot, policy):
    stock = {(d.id, f): d.inventory[f] for d in snapshot.depots for f in FUELS}
    dispatch = {d.id: d.dispatch_capacity_per_tick * (
        policy["constrained_dispatch_factor"] if d.status == "CONSTRAINED" else 1)
        - sum(a.quantity for a in snapshot.allocations if a.source_depot_id == d.id and a.status in ACTIVE)
        for d in snapshot.depots}
    space = {(s.id, f): s.capacity[f] - s.inventory[f] - sum(q for _, q in inbound(snapshot, s.id, f))
             for s in snapshot.stations for f in FUELS}
    return stock, dispatch, space


def validate_batch(snapshot, bodies, policy=None):
    """Return first violated constraint; account for every order in the batch."""
    policy = policy or POLICY
    stock, dispatch, space = budgets(snapshot, policy)
    routes = {r.id: r for r in snapshot.routes}
    stations = {s.id: s for s in snapshot.stations}
    depots = {d.id: d for d in snapshot.depots}
    for raw in bodies:
        body = AllocationBody.model_validate(raw)
        route, station, depot = (routes.get(body.route_id), stations.get(body.destination_station_id),
                                 depots.get(body.source_depot_id))
        if not route or not station or not depot:
            return "NOT_FOUND"
        if (route.source_depot_id, route.destination_station_id) != (depot.id, station.id):
            return "ROUTE_MISMATCH"
        if station.status != "OPEN":
            return "STATION_CLOSED"
        if route.status != "AVAILABLE":
            return "ROUTE_DISRUPTED"
        quantity, fuel = body.quantity, body.fuel_type
        for limit, code in ((route.max_shipment, "ROUTE_CAPACITY_EXCEEDED"),
                            (stock[(depot.id, fuel)], "INSUFFICIENT_INVENTORY"),
                            (space[(station.id, fuel)], "DESTINATION_CAPACITY_EXCEEDED"),
                            (dispatch[depot.id], "DISPATCH_CAPACITY_EXCEEDED")):
            if quantity > limit + 1e-6:
                return code
        stock[(depot.id, fuel)] -= quantity
        space[(station.id, fuel)] -= quantity
        dispatch[depot.id] -= quantity
    return None


def recommend(snapshot, analysis, policy=None, paths=300, policy_name="greedy_v1"):
    policy = policy or POLICY
    if policy_name == "do_nothing":
        return []
    stock, dispatch, space = budgets(snapshot, policy)
    depots = {d.id: d for d in snapshot.depots}
    candidates = []
    for station in snapshot.stations:
        if station.status != "OPEN":
            continue
        for fuel in FUELS:
            a = analysis[(station.id, fuel)]
            needs_order = (station.inventory[fuel] / station.capacity[fuel] < 0.4 if policy_name == "naive_reorder"
                           else a["risk"] >= 0.2 or a["hours_to_stockout"] is not None)
            if needs_order and a["known"]:
                candidates.append((station, fuel, a))
    candidates.sort(key=lambda c: (-policy["weights"][c[1]] * c[2]["risk"], c[2]["hours_to_stockout"] or 999))
    total_need = sum(max(0, a["forecast_12h"] - s.inventory[f] - sum(q for _, q in a["inbound"]))
                     for s, f, a in candidates)
    rationing = total_need > sum(stock.values())
    planned = []
    for station, fuel, a in candidates:
        routes = [r for r in snapshot.routes if r.destination_station_id == station.id and r.status == "AVAILABLE"]
        routes.sort(key=lambda r: (
            bool(a["hours_to_stockout"] is not None and (r.transit_ticks + 1) * snapshot.instance.tick_minutes / 60 > a["hours_to_stockout"]),
            depots[r.source_depot_id].region_id != station.region_id, r.transit_ticks))
        target_hours = 12 if rationing else policy["target_cover_hours"]
        target = sum(a["mean"][:int(target_hours * 60 / snapshot.instance.tick_minutes)])
        need = max(0, target - station.inventory[fuel] - sum(q for _, q in a["inbound"]))
        if policy_name == "naive_reorder":
            need = max(0, space[(station.id, fuel)])
        extra_arrivals = []
        for route in routes:
            depot = depots[route.source_depot_id]
            cross_region = depot.region_id != station.region_id
            if policy_name == "naive_reorder" and cross_region:
                continue
            reserve = 0.0
            if cross_region:
                reserve = sum(max(0, analysis[(s.id, fuel)]["forecast_12h"] - s.inventory[fuel]
                                  - sum(q for _, q in inbound(snapshot, s.id, fuel)))
                              for s in snapshot.stations if s.region_id == depot.region_id)
            while need >= policy["minimum_shipment"]:
                quantity = int(min(need, route.max_shipment, stock[(depot.id, fuel)] - reserve,
                                   dispatch[depot.id], space[(station.id, fuel)]))
                if quantity < policy["minimum_shipment"]:
                    break
                eta = snapshot.instance.tick + 1 + route.transit_ticks
                length = len(a["inventory_path"])

                # Displayed impact is INDEPENDENT: only already-approved simulator shipments
                # (pending / in transit) plus this single proposal are counted. Sibling
                # proposals from the same cycle are unapproved, so they must never silently
                # inflate this recommendation's benefit. They are reported separately.
                def whatif(q, arrival=eta, siblings=()):
                    return project(station.inventory[fuel], station.capacity[fuel], a["mean"][:length],
                                   a["std"][:length], a["inbound"] + list(siblings) + [(arrival, q)],
                                   snapshot.instance.tick, snapshot.instance.tick_minutes,
                                   seed_for(snapshot, station.id, fuel), paths)

                def coordinated(siblings):
                    return project(station.inventory[fuel], station.capacity[fuel], a["mean"][:length],
                                   a["std"][:length], a["inbound"] + list(siblings) + [(eta, quantity)],
                                   snapshot.instance.tick, snapshot.instance.tick_minutes,
                                   seed_for(snapshot, station.id, fuel), paths)

                before, after, half = whatif(0), whatif(quantity), whatif(quantity / 2)
                crisis = any(e.status == "ACTIVE" and event_applies(e, station, depot, route) for e in snapshot.events)
                score = max(0, 0.8 - 0.2 * crisis - 0.1 * cross_region - 0.1 * rationing)
                review_reasons = []
                if crisis:
                    review_reasons.append("crisis_event")
                if cross_region:
                    review_reasons.append("cross_region_transfer")
                if rationing:
                    review_reasons.append("network_rationing")
                if score < 0.75:
                    review_reasons.append("low_confidence")
                alternatives = [{"label": "Half shipment", "quantity": quantity / 2, "risk_after": half["risk"]},
                                {"label": "Do nothing", "quantity": 0, "risk_after": before["risk"]}]
                for other in routes:
                    other_depot = depots[other.source_depot_id]
                    alt = {"idempotency_key": "what-if", "source_depot_id": other.source_depot_id,
                           "destination_station_id": station.id, "route_id": other.id,
                           "fuel_type": fuel, "quantity": quantity}
                    if other.id != route.id and not validate_batch(snapshot, [alt], policy):
                        alt_risk = whatif(quantity, snapshot.instance.tick + 1 + other.transit_ticks)
                        alternatives.append({"label": f"Via {other_depot.name}", "route_id": other.id,
                                             "risk_after": alt_risk["risk"]})
                        break
                plan = coordinated(extra_arrivals)
                recommendation = {
                    "created_tick": snapshot.instance.tick, "station_id": station.id, "fuel_type": fuel,
                    "current_inventory": station.inventory[fuel], "projected_stockout_hours": a["hours_to_stockout"],
                    "expected_demand_12h": a["forecast_12h"],
                    "action": {"source_depot_id": depot.id, "route_id": route.id, "quantity": quantity, "eta_tick": eta},
                    "impact": {"risk_before": before["risk"], "risk_after": after["risk"],
                               "unmet_before_l": before["expected_unmet_liters"], "unmet_after_l": after["expected_unmet_liters"],
                               "basis": "This shipment only. Sibling proposals are excluded and listed under plan."},
                    "confidence": {"score": round(score, 2), "level": "HIGH" if score >= 0.75 else "MEDIUM" if score >= 0.5 else "LOW"},
                    "requires_review": bool(review_reasons),
                    "review_reasons": review_reasons,
                    "rationing": rationing,
                    "plan": {"shipments_for_entity": 1 + len(extra_arrivals),
                             "sibling_proposals": len(extra_arrivals),
                             "risk_after_all_approved": plan["risk"],
                             "unmet_after_all_approved_l": plan["expected_unmet_liters"],
                             "note": "Coordinated figures assume EVERY proposal for this station and fuel is approved. "
                                     "Each individual approval is validated on its own against live depot, dispatch, "
                                     "route and tank limits, so a partial approval ships less than shown here."},
                    "signals": [f"Model estimates {before['risk']:.0%} stockout risk over 12 hours.",
                                f"Demand multiplier {station.demand_multiplier:.2f}; forecast source: {a['source']}.",
                                "Rationing to spread limited supply." if rationing else f"Target cover: {target_hours} hours."],
                    "constraints": [f"Route limit {route.max_shipment:,.0f} L", f"Unreserved tank space {space[(station.id, fuel)]:,.0f} L",
                                    f"Dispatch available {max(0, dispatch[depot.id]):,.0f} L", f"Home-region reserve {reserve:,.0f} L"],
                    "alternatives": alternatives,
                    "explanation": f"Send {quantity:,.0f} L of {fuel.lower()} from {depot.name} to {station.name}. "
                                   f"Arrival at tick {eta}; estimated stockout risk {before['risk']:.0%} → {after['risk']:.0%}. "
                                   "Impact is a forecast, not a measured simulator outcome.",
                    "explanation_source": "template", "policy_version": policy_name, "model_version": a["model_version"],
                }
                planned.append(recommendation)
                extra_arrivals.append((eta, quantity))
                need -= quantity
                stock[(depot.id, fuel)] -= quantity
                space[(station.id, fuel)] -= quantity
                dispatch[depot.id] -= quantity
    return planned


def reevaluate_review(snapshot, payload):
    """Recompute human-review requirements against the state that will actually be used.

    A recommendation is proposed against one snapshot and approved against another. A crisis
    that started in between, or a tighter tank, must escalate the review requirement rather
    than inherit a stale "no review needed". Returns the reasons and whether anything was
    added since the proposal was created.
    """
    reasons = list(payload.get("review_reasons") or [])
    original = set(reasons)
    station = next((s for s in snapshot.stations if s.id == payload["station_id"]), None)
    action = payload.get("action") or {}
    depot = next((d for d in snapshot.depots if d.id == action.get("source_depot_id")), None)
    route = next((r for r in snapshot.routes if r.id == action.get("route_id")), None)
    if station is None or depot is None or route is None:
        return sorted(original | {"entity_missing"}), True
    if any(e.status == "ACTIVE" and event_applies(e, station, depot, route) for e in snapshot.events):
        reasons.append("crisis_event")
    if station.status != "OPEN":
        reasons.append("station_unavailable")
    if route.status != "AVAILABLE":
        reasons.append("route_disrupted")
    reasons = sorted(set(reasons))
    return reasons, bool(set(reasons) - original)


def inventory_reconciliation(snapshot, previous, relative_tolerance=0.02, absolute_tolerance=25.0):
    """Explain tank movement between two snapshots from served demand and arrived allocations.

    Returns one record per station/fuel whose observed change cannot be accounted for.
    Stations that were in outage, and intervals with missing history, are excluded rather
    than reported, because reconciliation is not reliable there.
    """
    if previous is None:
        return []
    low, high = previous.instance.tick, snapshot.instance.tick
    if high <= low or not previous.consistent or not snapshot.consistent:
        return []
    served = {}
    ticks = {}
    for row in snapshot.history:
        if low < row.tick <= high:
            key = (row.station_id, row.fuel_type)
            if row.tick in ticks.setdefault(key, set()):
                continue
            ticks[key].add(row.tick)
            served[key] = served.get(key, 0.0) + row.served_liters
    arrived = {}
    for allocation in snapshot.allocations:
        if allocation.destination_station_id is None or allocation.actual_arrival_tick is None:
            continue
        if low < allocation.actual_arrival_tick <= high and allocation.status == "ARRIVED":
            key = (allocation.destination_station_id, allocation.fuel_type)
            arrived[key] = arrived.get(key, 0.0) + allocation.quantity
    out = []
    for station in snapshot.stations:
        before = next((s for s in previous.stations if s.id == station.id), None)
        if before is None or station.status == "OUTAGE" or before.status == "OUTAGE":
            continue
        for fuel in FUELS:
            key = (station.id, fuel)
            if ticks.get(key, set()) != set(range(low + 1, high + 1)):
                continue  # Require complete history for this tank, not unrelated tanks.
            predicted = max(0.0, before.inventory[fuel] - served.get(key, 0.0) + arrived.get(key, 0.0))
            actual = station.inventory[fuel]
            tolerance = max(absolute_tolerance, predicted * relative_tolerance)
            delta = actual - predicted
            if abs(delta) > tolerance:
                out.append({"station_id": station.id, "fuel_type": fuel, "expected": round(predicted, 2),
                            "actual": round(actual, 2), "delta": round(delta, 2),
                            "served_liters": round(served.get(key, 0.0), 2),
                            "arrived_liters": round(arrived.get(key, 0.0), 2)})
    return out


def detect(snapshot, analysis, previous=None, unexplained_inventory=()):
    alerts = []

    def add(kind, entity, message, severity="WARNING", fuel=None, events=()):
        alerts.append({"key": f"{kind}:{entity}:{fuel or '-'}", "type": kind, "entity_id": entity,
                       "fuel_type": fuel, "severity": severity, "message": message,
                       "last_tick": snapshot.instance.tick, "event_ids": list(events)})

    old_supply = {s.id: s for s in previous.supply} if previous else {}
    old_stations = {s.id: s for s in previous.stations} if previous else {}
    for station in snapshot.stations:
        station_events = scoped_event_ids(snapshot, station=station)
        if station.status == "OUTAGE":
            add("station_outage", station.id, f"{station.name} is unavailable; allocations paused.", "CRITICAL",
                events=station_events)
        old = old_stations.get(station.id)
        if station.demand_multiplier > 1.2 or (old and abs(old.demand_multiplier - station.demand_multiplier) > 0.1):
            add("demand_anomaly", station.id, f"{station.name}: demand multiplier {station.demand_multiplier:.2f}.",
                events=scoped_event_ids(snapshot, station=station))
        for fuel in FUELS:
            a = analysis[(station.id, fuel)]
            if a["risk"] >= 0.5:
                add("shortage", station.id, f"{station.name} {fuel}: {a['risk']:.0%} estimated stockout risk.",
                    a["risk_level"], fuel, events=station_events)
            if not a["known"]:
                add("unknown_profile", station.id, "Unknown demand profile with no history; forecasting requires review.",
                    "WARNING", fuel, events=station_events)
    for route in snapshot.routes:
        if route.status == "DISRUPTED":
            add("route_disruption", route.id, f"Route {route.id} is disrupted; use available alternatives.",
                events=scoped_event_ids(snapshot, route=route))
    depot_ids = {d.id: d for d in snapshot.depots}
    for supply in snapshot.supply:
        supply_events = scoped_event_ids(snapshot, depot=depot_ids.get(supply.depot_id))
        if supply.status == "DELAYED" or (supply.status != "ARRIVED" and supply.planned_tick < snapshot.instance.tick):
            add("supply_delay", supply.id, f"Incoming {supply.fuel_type.lower()} delivery is delayed.", events=supply_events)
        old = old_supply.get(supply.id)
        if old and supply.quantity < old.quantity - 0.01:
            add("supply_shortfall", supply.id, f"Delivery reduced from {old.quantity:,.0f} to {supply.quantity:,.0f} L.",
                events=supply_events)
    for depot in snapshot.depots:
        used = sum(a.quantity for a in snapshot.allocations if a.source_depot_id == depot.id and a.status in ACTIVE)
        if depot.status == "CONSTRAINED" or used > depot.dispatch_capacity_per_tick * 0.8:
            add("depot_bottleneck", depot.id, f"{depot.name}: {depot.status.lower()}, {used:,.0f} L dispatch committed.",
                events=scoped_event_ids(snapshot, depot=depot))
    for region in snapshot.regions:
        high = sum(analysis[(s.id, f)]["risk"] >= 0.5 for s in snapshot.stations if s.region_id == region.id for f in FUELS)
        if high >= 2:
            add("regional_shortage", region.id, f"{region.name}: {high} station/fuel combinations have high shortage risk.",
                "HIGH", events=scoped_event_ids(snapshot, region=region))
    for row in unexplained_inventory:
        station = next((s for s in snapshot.stations if s.id == row["station_id"]), None)
        add("inventory_anomaly", row["station_id"],
            f"{station.name if station else row['station_id']} {row['fuel_type']}: tank holds {row['actual']:,.0f} L "
            f"but served demand and arrivals explain {row['expected']:,.0f} L "
            f"({row['delta']:+,.0f} L unexplained).",
            "WARNING", row["fuel_type"], events=scoped_event_ids(snapshot, station=station))
    return alerts
