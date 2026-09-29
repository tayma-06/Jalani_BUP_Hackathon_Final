import copy
import random

import pytest
from pydantic import ValidationError

from app.intelligence.engine import analyse, project, recommend, validate_batch
from app.sim.schemas import Snapshot


def bodies(plans):
    return [{"idempotency_key": str(i), "source_depot_id": p["action"]["source_depot_id"],
             "destination_station_id": p["station_id"], "route_id": p["action"]["route_id"],
             "fuel_type": p["fuel_type"], "quantity": p["action"]["quantity"]} for i, p in enumerate(plans)]


def test_arrival_timing_and_unmet_are_hand_checkable():
    # Tick 1 consumes 5 from 8; tick 2 receives 3 before consuming 5; tick 3 misses 4.
    result = project(8, 100, [5, 5, 5], [0, 0, 0], [(2, 3)], 0, 15, 1, 20)
    assert result["inventory_path"] == [3, 1, 0]
    assert result["expected_unmet_liters"] == 4
    assert result["hours_to_stockout"] == 0.75
    assert result["risk"] == 1


def test_zero_demand_and_zero_stock_have_zero_stockout_risk():
    assert project(0, 100, [0, 0], [0, 0], [], 0, 15, 1, 20)["risk"] == 0


async def test_batch_constraints_hold_in_randomized_worlds(service):
    rng = random.Random(8)
    original = service.snapshot.model_dump(mode="json")
    for _ in range(18):
        data = copy.deepcopy(original)
        for s in data["stations"]:
            for f in s["inventory"]:
                s["inventory"][f] = rng.uniform(0, s["capacity"][f])
        for d in data["depots"]:
            d["dispatch_capacity_per_tick"] = rng.uniform(500, 12000)
            for f in d["inventory"]:
                d["inventory"][f] = rng.uniform(0, d["capacity"][f])
        for r in data["routes"]:
            r["status"] = rng.choice(["AVAILABLE", "AVAILABLE", "DISRUPTED"])
        snapshot = Snapshot.model_validate(data)
        analysis = analyse(snapshot, paths=30)
        for policy in ["naive_reorder", "greedy_v1"]:
            plans = recommend(snapshot, analysis, paths=30, policy_name=policy)
            assert validate_batch(snapshot, bodies(plans)) is None


async def test_pending_tank_reservations_and_dispatch_are_counted(service, fake):
    rec = service.recommendations[0]
    await service.approve(rec["id"], "alice")
    a = fake.data["allocations"][0]
    snapshot = service.snapshot
    invalid = dict(a)
    invalid["idempotency_key"] = "different"
    invalid["quantity"] = 100000
    assert validate_batch(snapshot, [invalid]) == "ROUTE_CAPACITY_EXCEEDED"
    plans = recommend(snapshot, analyse(snapshot, paths=30), paths=30)
    assert validate_batch(snapshot, bodies(plans)) is None


async def test_unknown_route_and_nonfinite_inventory_fail_validation(service):
    data = service.snapshot.model_dump(mode="json")
    data["routes"][0]["source_depot_id"] = "missing"
    with pytest.raises(ValidationError):
        Snapshot.model_validate(data)
    data = service.snapshot.model_dump(mode="json")
    data["stations"][0]["inventory"]["DIESEL"] = float("nan")
    with pytest.raises(ValidationError):
        Snapshot.model_validate(data)


async def test_common_seed_whatif_never_worsens_unmet(service):
    for plan in service.recommendations:
        assert plan["impact"]["unmet_after_l"] <= plan["impact"]["unmet_before_l"]
        assert plan["impact"]["risk_after"] <= plan["impact"]["risk_before"]
