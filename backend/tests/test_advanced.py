"""Regression tests for the corrections made to already-implemented advanced features."""
import asyncio
import copy

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db import Audit, PolicyVersion, Recommendation
from app.intelligence.engine import (
    analyse,
    detect,
    inventory_reconciliation,
    recommend,
    reevaluate_review,
    scoped_event_ids,
)
from app.policy_registry import (
    PolicyError,
    history,
    register,
    rollback,
    supersede_stale_recommendations,
)
from app.sim.schemas import Snapshot


def world_at(tick=24):
    from tests.fake_simulator import world
    data = world()
    data["instance"]["tick"] = tick
    data["instance"]["sim_time"] = f"2026-01-01T{tick // 4:02d}:{(tick % 4) * 15:02d}:00+00:00"
    data["supply"] = data.pop("supply-arrivals")
    data["history"] = data.pop("demand-history")
    data["start_tick"] = 0
    data["end_tick"] = tick
    return data


def with_event(data, event_id, event_type="demand_spike", parameters=None, start=0, end=200):
    data["events"].append({"id": event_id, "type": event_type, "start_tick": start, "end_tick": end,
                           "status": "ACTIVE", "parameters": parameters or {}})
    return data


# --- C: alerts must reference only events that actually reach the entity -------------------

def test_alert_events_are_scoped_to_the_affected_entity():
    # A crisis confined to the Chattogram region must not appear on a Dhaka station.
    data = with_event(world_at(), 1, parameters={"region_ids": ["region-chattogram"], "multiplier": 3.0})
    snapshot = Snapshot.model_validate(data)
    ids = {s.id: scoped_event_ids(snapshot, station=s) for s in snapshot.stations}
    assert ids["station-karnaphuli"] == [1]
    assert ids["station-coxsbazar"] == [1]
    assert ids["station-mirpur"] == []
    assert ids["station-tongi"] == []


def test_route_and_depot_alerts_only_carry_their_own_events():
    data = with_event(world_at(), 7, "route_disruption", {"route_ids": ["route-gazipur-mirpur"]})
    snapshot = Snapshot.model_validate(data)
    route = next(r for r in snapshot.routes if r.id == "route-gazipur-mirpur")
    other = next(r for r in snapshot.routes if r.id != "route-gazipur-mirpur")
    assert scoped_event_ids(snapshot, route=route) == [7]
    assert scoped_event_ids(snapshot, route=other) == []
    depot = next(d for d in snapshot.depots if d.id == "depot-patiya")
    assert scoped_event_ids(snapshot, depot=depot) == []


def test_unrelated_alert_never_lists_an_unrelated_crisis():
    data = with_event(world_at(), 42, parameters={"station_ids": ["station-coxsbazar"], "multiplier": 2.0})
    snapshot = Snapshot.model_validate(data)
    analysis = analyse(snapshot, paths=20)
    for station in snapshot.stations:
        snapshot_data = copy.deepcopy(data)
        snapshot_data["stations"] = [s for s in snapshot_data["stations"] if s["id"] == station.id]
    alerts = detect(snapshot, analysis, None)
    for alert in alerts:
        if alert["entity_id"] in {"station-mirpur", "station-tongi", "station-karnaphuli"}:
            assert 42 not in alert["event_ids"], alert


def test_demand_spike_is_cleared_from_alerts_once_the_event_expires():
    data = with_event(world_at(), 3, parameters={"station_ids": ["station-mirpur"], "multiplier": 2.0}, start=0, end=5)
    data["stations"][0]["demand_multiplier"] = 2.0
    snapshot = Snapshot.model_validate(data)
    analysis = analyse(snapshot, paths=20)
    while_active = {a["key"]: a for a in detect(snapshot, analysis, None)}
    assert 3 in while_active["demand_anomaly:station-mirpur:-"]["event_ids"]
    # The multiplier is still high, but the event that explains it is gone: an alert must not
    # keep claiming the count the crisis contributes to its reasoning.
    data["events"][0].update({"status": "RESOLVED", "start_tick": 100, "end_tick": 200})
    after = detect(Snapshot.model_validate(data), analysis, None)
    assert 3 not in {a["key"]: a for a in after}["demand_anomaly:station-mirpur:-"]["event_ids"]


# --- C: unexplained inventory changes where reconciliation is reliable ----------------------

def reconcile(previous_tick, current_tick, served, arrived=(), outage=False, gap=False):
    previous = Snapshot.model_validate(world_at(previous_tick))
    data = world_at(current_tick)
    station_id, fuel = "station-mirpur", "DIESEL"
    start = previous.stations[0].inventory[fuel]
    history = [{"id": i, "station_id": station_id, "fuel_type": fuel, "tick": previous_tick + i + 1,
                "sim_time": previous.instance.sim_time.isoformat(), "demand_liters": served,
                "served_liters": served, "unmet_liters": 0.0, "demand_multiplier": 1.0}
               for i in range(current_tick - previous_tick)]
    data["history"] = history
    data["stations"][0]["inventory"][fuel] = max(0.0, start - served * (current_tick - previous_tick))
    allocations = [{"idempotency_key": f"k{i}", "source_depot_id": d["id"], "destination_station_id": station_id,
                    "route_id": r["id"], "fuel_type": fuel, "quantity": q, "id": i + 1,
                    "created_tick": previous_tick, "departure_tick": previous_tick,
                    "expected_arrival_tick": current_tick, "actual_arrival_tick": current_tick,
                    "status": "ARRIVED", "failure_reason": None}
                   for i, (d, r, q) in enumerate(arrived)]
    data["allocations"] = allocations
    if outage:
        data["stations"][0]["status"] = "OUTAGE"
    current = Snapshot.model_validate(data)
    if gap:
        current.history = current.history[:1]
    return previous, current


def test_inventory_movement_explained_by_demand_and_arrivals_is_not_alerted():
    previous, current = reconcile(10, 12, served=300.0,
                                  arrived=[({"id": "depot-gazipur"}, {"id": "route-gazipur-mirpur"}, 900.0)])
    # inventory: 2800 - 600 (two ticks of 300) + 900 = 3400
    current.stations[0].inventory["DIESEL"] = 2800 - 600 + 900
    assert inventory_reconciliation(current, previous) == []


def test_unexplained_inventory_gain_raises_one_scoped_alert():
    previous, current = reconcile(10, 12, served=300.0)
    current.stations[0].inventory["DIESEL"] += 750.0
    rows = inventory_reconciliation(current, previous)
    assert len(rows) == 1
    assert rows[0]["station_id"] == "station-mirpur" and rows[0]["fuel_type"] == "DIESEL"
    assert rows[0]["delta"] == pytest.approx(750.0)
    alerts = detect(current, analyse(current, paths=20), previous, rows)
    anomaly = [a for a in alerts if a["type"] == "inventory_anomaly"]
    assert len(anomaly) == 1 and anomaly[0]["entity_id"] == "station-mirpur"


def test_reconciliation_is_skipped_for_outages_and_missing_history():
    previous, current = reconcile(10, 12, served=300.0, outage=True)
    current.stations[0].inventory["DIESEL"] += 5000.0
    assert inventory_reconciliation(current, previous) == []
    previous, current = reconcile(10, 12, served=300.0, gap=True)
    current.stations[0].inventory["DIESEL"] += 5000.0
    assert inventory_reconciliation(current, previous) == []


# --- B: counterfactuals must be independent of unapproved sibling shipments ----------------

def starved_snapshots(quantity_per_shipment, station=0, fuel="DIESEL"):
    data = world_at()
    s = data["stations"][station]
    s["inventory"][fuel] = 200
    s["capacity"][fuel] = 4000
    for d in data["depots"]:
        d["inventory"][fuel] = 60000
        d["dispatch_capacity_per_tick"] = 40000
    for r in data["routes"]:
        r["max_shipment"] = quantity_per_shipment
    return Snapshot.model_validate(data)


def test_shown_benefit_ignores_unapproved_sibling_shipments():
    snapshot = starved_snapshots(1500)
    analysis = analyse(snapshot, paths=40)
    plans = [p for p in recommend(snapshot, analysis, paths=40) if p["station_id"] == "station-mirpur"
             and p["fuel_type"] == "DIESEL"]
    assert len(plans) > 1, "this fixture must produce a multi-shipment plan"
    first = plans[0]
    assert first["impact"]["basis"].startswith("This shipment only")
    # A sibling shipment is disclosed, but the headline number must be the single-shipment one.
    assert first["plan"]["sibling_proposals"] == 0
    assert first["plan"]["risk_after_all_approved"] <= first["impact"]["risk_after"] + 1e-9
    later = plans[1]
    assert later["plan"]["sibling_proposals"] == 1
    # "Do nothing" for the second proposal must not credit the first, unapproved shipment.
    do_nothing = next(a for a in later["alternatives"] if a["label"] == "Do nothing")
    assert do_nothing["risk_after"] == pytest.approx(later["impact"]["risk_before"])


def test_independent_impact_is_never_worse_than_dependent_counterfactual():
    snapshot = starved_snapshots(1500)
    analysis = analyse(snapshot, paths=40)
    for plan in recommend(snapshot, analysis, paths=40):
        if plan["station_id"] != "station-mirpur" or plan["fuel_type"] != "DIESEL":
            continue
        assert plan["impact"]["unmet_after_l"] <= plan["impact"]["unmet_before_l"]
        # Crediting a sibling can only ever look better; it must not be what we display.
        assert plan["impact"]["risk_after"] >= plan["plan"]["risk_after_all_approved"] - 1e-9


# --- duplicate proposals and rejected-proposal cooldown ------------------------------------

async def test_only_one_proposal_per_station_and_fuel_is_persisted(service):
    starved = starved_snapshots(1200)
    analysis = analyse(starved, paths=20)
    plans = [p for p in recommend(starved, analysis, paths=20) if p["station_id"] == "station-mirpur"
             and p["fuel_type"] == "DIESEL"]
    assert len(plans) > 1
    service.persist_intelligence(starved, [], plans)
    with service.db.session() as session:
        rows = [r for r in session.scalars(select(Recommendation).where(
            Recommendation.run_id == service.run_id, Recommendation.status == "PROPOSED"))]
    pairs = [(r.payload["station_id"], r.payload["fuel_type"]) for r in rows]
    assert len(pairs) == len(set(pairs)), pairs


async def clear_proposals(service, station_id=None, fuel=None):
    """Free the (station, fuel) slot so the cooldown path, not the duplicate guard, is tested."""
    with service.db.session() as session:
        for row in session.scalars(select(Recommendation).where(Recommendation.status == "PROPOSED")):
            if station_id and (row.payload["station_id"] != station_id or
                               (fuel and row.payload["fuel_type"] != fuel)):
                continue
            row.status = "EXPIRED"
        session.commit()


async def test_rejected_proposal_is_not_immediately_repeated(service):
    starved = starved_snapshots(6000)
    plans = [p for p in recommend(starved, analyse(starved, paths=20), paths=20)
             if p["station_id"] == "station-mirpur" and p["fuel_type"] == "DIESEL"]
    assert plans
    await clear_proposals(service, "station-mirpur", "DIESEL")
    with service.db.session() as session:
        row = Recommendation(run_id=service.run_id, created_tick=starved.instance.tick,
                             status="REJECTED", payload=plans[0])
        session.add(row)
        session.commit()
    service.persist_intelligence(starved, [], plans)
    with service.db.session() as session:
        proposed = [r for r in session.scalars(select(Recommendation).where(
            Recommendation.run_id == service.run_id, Recommendation.status == "PROPOSED"))
            if r.payload["station_id"] == "station-mirpur" and r.payload["fuel_type"] == "DIESEL"]
        rejected = session.scalar(select(Recommendation).where(
            Recommendation.run_id == service.run_id, Recommendation.status == "REJECTED"))
    assert proposed == []
    assert "Cooldown ends" in rejected.payload["suppression"]
    assert rejected.payload["suppressed_until_tick"] == starved.instance.tick + service.config.rejection_cooldown_ticks


async def test_rejection_cooldown_releases_when_risk_worsens(service):
    starved = starved_snapshots(6000)
    starved.stations[0].inventory["DIESEL"] = 3000
    plans = [p for p in recommend(starved, analyse(starved, paths=20), paths=20)
             if p["station_id"] == "station-mirpur" and p["fuel_type"] == "DIESEL"]
    worse = copy.deepcopy(starved)
    worse.stations[0].inventory["DIESEL"] = 0
    new_plans = [p for p in recommend(worse, analyse(worse, paths=20), paths=20)
                 if p["station_id"] == "station-mirpur" and p["fuel_type"] == "DIESEL"]
    assert new_plans[0]["impact"]["unmet_before_l"] > plans[0]["impact"]["unmet_before_l"] + 500
    await clear_proposals(service, "station-mirpur", "DIESEL")
    with service.db.session() as session:
        session.add(Recommendation(run_id=service.run_id, created_tick=worse.instance.tick,
                                   status="REJECTED", payload=plans[0]))
        session.commit()
    service.persist_intelligence(worse, [], new_plans)
    with service.db.session() as session:
        proposed = [r for r in session.scalars(select(Recommendation).where(
            Recommendation.run_id == service.run_id, Recommendation.status == "PROPOSED"))
            if r.payload["station_id"] == "station-mirpur" and r.payload["fuel_type"] == "DIESEL"]
    assert proposed, "a materially worse risk must be re-proposed despite a recent rejection"


async def test_cooldown_expires_on_its_own_after_the_documented_window(service):
    starved = starved_snapshots(6000)
    plans = [p for p in recommend(starved, analyse(starved, paths=20), paths=20)
             if p["station_id"] == "station-mirpur" and p["fuel_type"] == "DIESEL"]
    later = copy.deepcopy(starved)
    later.instance.tick += service.config.rejection_cooldown_ticks + 1
    await clear_proposals(service, "station-mirpur", "DIESEL")
    with service.db.session() as session:
        session.add(Recommendation(run_id=service.run_id, created_tick=starved.instance.tick,
                                   status="REJECTED", payload=plans[0]))
        session.commit()
    service.persist_intelligence(later, [], plans)
    with service.db.session() as session:
        proposed = [r for r in session.scalars(select(Recommendation).where(
            Recommendation.run_id == service.run_id, Recommendation.status == "PROPOSED"))
            if r.payload["station_id"] == "station-mirpur" and r.payload["fuel_type"] == "DIESEL"]
    assert proposed


# --- D: versioned policy registry, rollback and revalidation --------------------------------

def registry_config(version, diesel=1.3):
    return {"version": version, "target_cover_hours": 24, "minimum_shipment": 500,
            "constrained_dispatch_factor": 0.5, "weights": {"DIESEL": diesel, "PETROL": 1.0, "OCTANE": 0.8}}


def test_registry_records_activation_actor_reason_and_predecessor(service):
    with service.db.session() as session:
        first, changed = register(session, "greedy_v1", registry_config("greedy_v1"), "alice", "baseline")
        assert changed and first.previous_version is None
        second, changed = register(session, "naive_reorder", registry_config("naive_reorder", 2.0), "bob", "trial")
        assert changed and second.previous_version == "greedy_v1"
        session.commit()
        rows = history(session)
    assert [r["version"] for r in rows] == ["naive_reorder", "greedy_v1"]
    assert rows[0]["actor"] == "bob" and rows[0]["reason"] == "trial"
    assert rows[0]["weights"]["DIESEL"] == 2.0
    assert [r["active"] for r in rows] == [True, False]


def test_reactivating_the_same_configuration_creates_no_version(service):
    with service.db.session() as session:
        register(session, "greedy_v1", registry_config("greedy_v1"), "alice", "baseline")
        session.commit()
        _, changed = register(session, "greedy_v1", registry_config("greedy_v1"), "alice", "again")
        session.commit()
        assert changed is False
        assert session.scalar(select(PolicyVersion).where(PolicyVersion.version == "greedy_v1")) is not None
        assert len(history(session)) == 1


def test_rollback_restores_the_intended_configuration_and_is_audited(service):
    with service.db.session() as session:
        register(session, "greedy_v1", registry_config("greedy_v1", 1.3), "alice", "baseline")
        register(session, "naive_reorder", registry_config("naive_reorder", 2.5), "bob", "trial")
        session.commit()
        target, previous = rollback(session, "carol", "trial was worse")
        session.commit()
        assert target.version == "greedy_v1" and previous == "naive_reorder"
        assert target.config["weights"]["DIESEL"] == 1.3
        assert target.reason == "ROLLBACK: trial was worse"
        entries = list(session.scalars(select(Audit)))
    assert any(a.action == "policy.rolled_back" and a.actor == "carol" for a in entries)
    assert any(a.action == "policy.activated" and a.target == "naive_reorder" for a in entries)


def test_rollback_without_a_predecessor_is_refused(service):
    with service.db.session() as session:
        register(session, "greedy_v1", registry_config("greedy_v1"), "alice", "baseline")
        session.commit()
        with pytest.raises(PolicyError, match="No earlier policy version"):
            rollback(session, "carol", "no-op")


def test_a_version_name_cannot_be_reused_for_a_different_configuration(service):
    with service.db.session() as session:
        register(session, "greedy_v1", registry_config("greedy_v1", 1.3), "alice", "baseline")
        session.commit()
        with pytest.raises(PolicyError, match="already exists"):
            register(session, "greedy_v1", registry_config("greedy_v1", 4.0), "alice", "different")


def test_policy_change_invalidates_outstanding_recommendations(service):
    with service.db.session() as session:
        existing = sum(r.status == "PROPOSED" and r.payload.get("policy_version") != "naive_reorder"
                       for r in session.scalars(select(Recommendation)))
        session.add(Recommendation(run_id=service.run_id, created_tick=1, status="PROPOSED",
                                   payload={"policy_version": "greedy_v1", "station_id": "s", "fuel_type": "DIESEL"}))
        session.add(Recommendation(run_id=service.run_id, created_tick=1, status="PROPOSED",
                                   payload={"policy_version": "naive_reorder", "station_id": "s", "fuel_type": "PETROL"}))
        session.commit()
        changed = supersede_stale_recommendations(session, service.run_id, "naive_reorder")
        session.commit()
        statuses = sorted(r.status for r in session.scalars(select(Recommendation))
                          if r.payload["station_id"] == "s")
    assert changed == existing + 1
    assert statuses == ["PROPOSED", "SUPERSEDED"]


async def test_service_rollback_switches_policy_and_supersedes_stale_proposals(service):
    service.activate_policy("greedy_v1", {**service.policy, "version": "greedy_v1"}, "alice", "baseline")
    first = service.activate_policy("naive_reorder", {**service.policy, "version": "naive_reorder"}, "bob", "trial")
    assert first["changed"] and first["previous_version"] == "greedy_v1"
    with service.db.session() as session:
        assert all(r.status == "SUPERSEDED" for r in session.scalars(
            select(Recommendation).where(Recommendation.run_id == service.run_id, Recommendation.status == "PROPOSED")))
    back = service.rollback_policy("carol", "trial regressed service level")
    assert back["version"] == "greedy_v1" and back["previous_version"] == "naive_reorder"
    assert service.policy_name == "greedy_v1"
    assert len(back["registry"]) == 2


# --- human review is re-evaluated immediately before execution -----------------------------

def test_review_escalates_when_a_crisis_starts_after_the_proposal():
    snapshot = Snapshot.model_validate(world_at())
    payload = {"station_id": "station-mirpur", "fuel_type": "DIESEL", "requires_review": False,
               "review_reasons": [], "action": {"source_depot_id": "depot-gazipur",
                                                "route_id": "route-gazipur-mirpur"}}
    reasons, newly = reevaluate_review(snapshot, payload)
    assert reasons == [] and newly is False
    snapshot = Snapshot.model_validate(with_event(world_at(), 5, parameters={"station_ids": ["station-mirpur"]}))
    reasons, newly = reevaluate_review(snapshot, payload)
    assert "crisis_event" in reasons and newly is True


def test_review_escalates_when_the_route_is_disrupted_after_the_proposal():
    data = world_at()
    data["routes"][0]["status"] = "DISRUPTED"
    payload = {"station_id": "station-mirpur", "fuel_type": "DIESEL", "requires_review": False,
               "review_reasons": [], "action": {"source_depot_id": "depot-gazipur", "route_id": data["routes"][0]["id"]}}
    reasons, newly = reevaluate_review(Snapshot.model_validate(data), payload)
    assert "route_disrupted" in reasons and newly is True


async def test_autopilot_is_blocked_when_review_escalates_after_the_proposal(service, fake):
    service.autopilot = "auto"
    rec = service.recommendations[0]
    with service.db.session() as session:
        row = session.get(Recommendation, rec["id"])
        row.payload = {**row.payload, "requires_review": False, "review_reasons": []}
        session.commit()
    fake.data["events"].append({"id": 1, "type": "demand_spike", "start_tick": 0, "end_tick": 999,
                                "status": "ACTIVE", "parameters": {"station_ids": [rec["station_id"]]}})
    with pytest.raises(HTTPException, match="Human review is required"):
        await service.approve(rec["id"], "AUTOPILOT")
    assert not fake.posts


async def test_concurrent_approval_still_yields_one_intent_after_changes(service, fake):
    rec = service.recommendations[0]
    first, second = await asyncio.gather(service.approve(rec["id"], "alice"), service.approve(rec["id"], "alice"))
    assert first["id"] == second["id"] and len(fake.posts) == 1
    assert first["request"] == fake.posts[0]
    assert first["request"]["idempotency_key"] == f"jal-{service.run_id}-{rec['id']}"
