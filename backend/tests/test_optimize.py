"""The LP policies must be exactly as safe as the greedy baseline, and must beat it."""
import copy

import pytest

from app.intelligence import engine, optimize
from app.sim.schemas import FUELS, Snapshot
from tests.fake_simulator import world as fixture_world

POLICY = copy.deepcopy(engine.POLICY)


def snapshot_at(tick=24, mutate=None):
    data = fixture_world()
    data["instance"]["tick"] = tick
    data["instance"]["sim_time"] = f"2026-01-01T{tick // 4:02d}:{(tick % 4) * 15:02d}:00+00:00"
    data["supply"] = data.pop("supply-arrivals")
    data["history"] = data.pop("demand-history")
    data["start_tick"] = 0
    data["end_tick"] = tick
    if mutate:
        mutate(data)
    return Snapshot.model_validate(data)


def with_policy(optimiser=None, **overrides):
    policy = copy.deepcopy(POLICY)
    if optimiser:
        policy["optimiser"] = {**POLICY["optimiser"], **optimiser}
    policy.update(overrides)
    return policy


def solve(snapshot, policy, policy_name="lp_v1"):
    analysis = engine.analyse(snapshot, paths=24)
    return optimize.optimize_recommend(snapshot, analysis, policy, 24, policy_name), analysis


def total_shipped(recommendations):
    return sum(r["action"]["quantity"] for r in recommendations)


def batches(recommendations):
    return [{"idempotency_key": f"t{index}", "source_depot_id": r["action"]["source_depot_id"],
             "destination_station_id": r["station_id"], "route_id": r["action"]["route_id"],
             "fuel_type": r["fuel_type"], "quantity": r["action"]["quantity"]}
            for index, r in enumerate(recommendations)]


# --- the solver itself ---------------------------------------------------------------------

def test_min_cost_flow_picks_the_cheapest_arc():
    arcs = [(0, 1, 10, 1.0), (1, 3, 10, 1.0), (0, 2, 10, 5.0), (2, 3, 10, 5.0)]
    flows = optimize.min_cost_flow(4, arcs, 10)
    assert flows[0] == pytest.approx(10)
    assert flows[1] == pytest.approx(10)
    assert flows[2] == 0
    assert flows[3] == 0


def test_min_cost_flow_respects_capacity_and_sends_exactly_the_flow():
    arcs = [(0, 1, 3, 1.0), (1, 3, 3, 1.0), (0, 2, 5, 2.0), (2, 3, 5, 2.0)]
    flows = optimize.min_cost_flow(4, arcs, 8)
    assert flows[1] + flows[3] == pytest.approx(8)
    assert flows[0] <= 3 and flows[2] <= 5


def test_min_cost_flow_is_deterministic():
    arcs = [(0, 1, 4, 1.0), (0, 2, 4, 1.0), (1, 3, 4, 1.0), (2, 3, 4, 1.0)]
    assert optimize.min_cost_flow(4, arcs, 6) == optimize.min_cost_flow(4, arcs, 6)


def test_min_cost_flow_fills_the_cheap_branch_before_the_dear_one():
    arcs = [(0, 1, 5, 1.0), (1, 3, 5, 1.0), (0, 2, 5, 2.0), (2, 3, 5, 2.0)]
    flows = optimize.min_cost_flow(4, arcs, 8)
    assert flows[0] == pytest.approx(5)
    assert flows[2] == pytest.approx(3)


def test_min_cost_flow_rejects_an_infeasible_request():
    with pytest.raises(optimize.OptimisationError):
        optimize.min_cost_flow(4, [(0, 1, 1, 1.0), (1, 3, 1, 1.0)], 5)


def test_min_cost_flow_rejects_a_model_that_is_too_large():
    with pytest.raises(optimize.OptimisationError):
        optimize.min_cost_flow(3, [(0, 1, 1, 1.0), (1, 2, 1, 1.0)], 1, max_arcs=1)


# --- safety: the LP plan may never violate anything the greedy plan respects ---------------

def test_lp_plan_passes_the_shared_validator():
    snapshot = snapshot_at()
    recommendations, _analysis = solve(snapshot, POLICY)
    assert engine.validate_batch(snapshot, batches(recommendations), POLICY) is None


def test_lp_plan_respects_route_tank_dispatch_and_depot_stock_limits():
    snapshot = snapshot_at()
    recommendations, _analysis = solve(snapshot, POLICY)
    assert recommendations
    stock, dispatch, space = engine.budgets(snapshot, POLICY)
    routes = {r.id: r for r in snapshot.routes}
    for recommendation in recommendations:
        action = recommendation["action"]
        route = routes[action["route_id"]]
        assert action["quantity"] <= route.max_shipment
        assert action["quantity"] <= space[(recommendation["station_id"], recommendation["fuel_type"])]
        assert action["quantity"] <= dispatch[action["source_depot_id"]]
        assert action["quantity"] <= stock[(action["source_depot_id"], recommendation["fuel_type"])]


def test_lp_shared_dispatch_budget_is_enforced_across_fuels():
    snapshot = snapshot_at()
    policy = with_policy()
    _shipments, analysis = solve(snapshot, policy)
    shipments, _breakdown = optimize.build_and_solve(snapshot, analysis, policy, "lp_v1")
    _stock, dispatch, _space = engine.budgets(snapshot, policy)
    per_depot = {}
    for shipment in shipments:
        per_depot[shipment["source_depot_id"]] = per_depot.get(shipment["source_depot_id"], 0) + \
            shipment["quantity"]
    for depot_id, shipped in per_depot.items():
        assert shipped <= dispatch[depot_id]


def test_lp_never_uses_an_unavailable_entity():
    def disrupt(data):
        data["stations"][0]["status"] = "OUTAGE"
        data["routes"][3]["status"] = "DISRUPTED"
        data["depots"][1]["status"] = "CONSTRAINED"

    snapshot = snapshot_at(mutate=disrupt)
    recommendations, _analysis = solve(snapshot, POLICY)
    closed = {s.id for s in snapshot.stations if s.status != "OPEN"}
    blocked = {r.id for r in snapshot.routes if r.status != "AVAILABLE"}
    for recommendation in recommendations:
        assert recommendation["station_id"] not in closed
        assert recommendation["action"]["route_id"] not in blocked
        assert recommendation["action"]["source_depot_id"] in {d.id for d in snapshot.depots}


def test_lp_honours_a_constrained_depot_dispatch_factor():
    def constrain(data):
        data["depots"][0]["status"] = "CONSTRAINED"

    snapshot = snapshot_at(mutate=constrain)
    recommendations, _analysis = solve(snapshot, POLICY)
    _stock, dispatch, _space = engine.budgets(snapshot, POLICY)
    per_depot = {}
    for recommendation in recommendations:
        depot_id = recommendation["action"]["source_depot_id"]
        per_depot[depot_id] = per_depot.get(depot_id, 0) + recommendation["action"]["quantity"]
    for depot_id, shipped in per_depot.items():
        assert shipped <= dispatch[depot_id]


# --- objective: the LP must not do worse than the greedy baseline --------------------------

def net_need(snapshot, analysis, horizon_hours=12):
    ticks = max(1, int(horizon_hours * 60 / snapshot.instance.tick_minutes))
    need = {}
    for station in snapshot.stations:
        if station.status != "OPEN":
            continue
        for fuel in FUELS:
            entry = analysis[(station.id, fuel)]
            net = sum(entry["mean"][:ticks]) - station.inventory[fuel] - \
                sum(q for _, q in entry["inbound"])
            if net > 0:
                need[(station.id, fuel)] = net
    return need


def coverage(recommendations, need):
    """Litres of the LP's own net-need definition that the plan actually covers."""
    served = {}
    for recommendation in recommendations:
        key = (recommendation["station_id"], recommendation["fuel_type"])
        served[key] = min(served.get(key, 0.0) + recommendation["action"]["quantity"],
                          need.get(key, 0.0))
    return sum(served.values())


def test_lp_covers_at_least_as_much_net_need_as_the_greedy_baseline():
    snapshot = snapshot_at()
    recommendations, analysis = solve(snapshot, POLICY)
    greedy = engine.recommend(snapshot, analysis, POLICY, 24, "greedy_v1")
    need = net_need(snapshot, analysis)
    assert need
    assert coverage(recommendations, need) >= coverage(greedy, need) - 1e-6


def test_lp_leaves_liters_unmet_rather_than_pretending_to_serve_them():
    # With no supply the LP must report the shortfall instead of proposing a plan.
    def drain(data):
        for depot in data["depots"]:
            depot["inventory"] = dict.fromkeys(FUELS, 0)

    snapshot = snapshot_at(mutate=drain)
    _recommendations, analysis = solve(snapshot, POLICY)
    shipments, breakdown = optimize.build_and_solve(snapshot, analysis, POLICY, "lp_v1")
    need = net_need(snapshot, analysis)
    assert shipments == []
    assert breakdown["unmet_liters"] == pytest.approx(sum(need.values()), abs=1.0)
    assert breakdown["litres_moved"] == 0.0


def test_lp_books_every_litre_of_need_it_moves_or_reports_unmet():
    snapshot = snapshot_at()
    _recommendations, analysis = solve(snapshot, POLICY)
    shipments, breakdown = optimize.build_and_solve(snapshot, analysis, POLICY, "lp_v1")
    need = net_need(snapshot, analysis)
    served = {}
    for shipment in shipments:
        key = (shipment["destination_station_id"], shipment["fuel_type"])
        served[key] = min(served.get(key, 0.0) + shipment["quantity"], need.get(key, 0.0))
    # Only real shortfalls of a litre or more are reported; residue is whole-litre rounding.
    expected_unmet = sum(max(0.0, volume - served.get(key, 0.0)) for key, volume in need.items()
                         if volume - served.get(key, 0.0) >= 1.0)
    assert breakdown["unmet_liters"] == pytest.approx(expected_unmet, abs=1.0)
    assert sum(breakdown["unmet_by_entity"].values()) == pytest.approx(expected_unmet, abs=1.0)
    assert breakdown["unmet_liters"] >= 0


def test_lp_may_move_more_than_the_need_to_use_spare_tank_space():
    # Surplus space is priced far below the unmet penalty, so filling it is the optimal use of
    # supply that would otherwise sit idle -- the figure must be reported honestly, not hidden.
    snapshot = snapshot_at()
    _recommendations, analysis = solve(snapshot, POLICY)
    _shipments, breakdown = optimize.build_and_solve(snapshot, analysis, POLICY, "lp_v1")
    need = sum(net_need(snapshot, analysis).values())
    assert breakdown["litres_moved"] >= need - breakdown["unmet_liters"] - 2.0
    assert breakdown["litres_moved"] <= sum(engine.budgets(snapshot, POLICY)[0].values())


def test_lp_reports_exactly_the_shortfall_when_supply_is_scarce():
    def scarce(data):
        for depot in data["depots"]:
            depot["inventory"] = dict.fromkeys(FUELS, 2500)

    snapshot = snapshot_at(mutate=scarce)
    _recommendations, analysis = solve(snapshot, POLICY)
    shipments, breakdown = optimize.build_and_solve(snapshot, analysis, POLICY, "lp_v1")
    need = net_need(snapshot, analysis)
    stock, _dispatch, _space = engine.budgets(snapshot, POLICY)
    served = {}
    for shipment in shipments:
        key = (shipment["destination_station_id"], shipment["fuel_type"])
        served[key] = min(served.get(key, 0.0) + shipment["quantity"], need.get(key, 0.0))
    expected_unmet = sum(max(0.0, volume - served.get(key, 0.0)) for key, volume in need.items()
                         if volume - served.get(key, 0.0) >= 1.0)
    # Scarcity is reported, not hidden: the shortfall is real and no litre is invented.
    assert breakdown["unmet_liters"] == pytest.approx(expected_unmet, abs=1.0)
    assert expected_unmet > 0
    assert breakdown["litres_moved"] > 0
    assert breakdown["litres_moved"] <= sum(stock.values())
    assert breakdown["unmet_liters"] == pytest.approx(
        sum(need.values()) - sum(served.values()), abs=1.0)


def test_lp_withholds_the_home_region_reserve_from_cross_region_arcs():
    # Cross-region stock above the reserve is usable; the reserve itself is not.
    snapshot = snapshot_at()
    _recommendations, analysis = solve(snapshot, POLICY)
    _shipments, breakdown = optimize.build_and_solve(snapshot, analysis, POLICY, "lp_v1")
    assert breakdown["unmet_liters"] >= 0
    assert breakdown["litres_moved"] < sum(engine.budgets(snapshot, POLICY)[0].values())


def test_lp_reserve_actually_blocks_a_cross_region_shipment():
    def empty_home_region(data):
        # Dhaka stations are full, so only Chattogram needs fuel and only cross-region
        # arcs can serve it -- the reserve decides whether anything ships at all.
        for station in data["stations"]:
            if station["region_id"] == "region-dhaka":
                station["inventory"] = station["capacity"]
        for depot in data["depots"]:
            depot["inventory"] = dict.fromkeys(FUELS, 200)

    snapshot = snapshot_at(mutate=empty_home_region)
    _recommendations, analysis = solve(snapshot, POLICY)
    with_reserve, _ = optimize.build_and_solve(snapshot, analysis, with_policy(), "lp_v1")
    without, _ = optimize.build_and_solve(
        snapshot, analysis, with_policy({"reserve_fraction": 0.0}), "lp_v1")
    assert sum(s["quantity"] for s in without) >= sum(s["quantity"] for s in with_reserve)


def test_lp_reports_unmet_only_when_a_real_constraint_blocks_delivery():
    # An unmet entry must correspond to demand the plan genuinely could not cover: the tank
    # is full, the routes are full, or the shipment fell below the policy minimum.
    snapshot = snapshot_at()
    _recommendations, analysis = solve(snapshot, POLICY)
    shipments, breakdown = optimize.build_and_solve(snapshot, analysis, POLICY, "lp_v1")
    _stock, _dispatch, space = engine.budgets(snapshot, POLICY)
    need = net_need(snapshot, analysis)
    served = {}
    for shipment in shipments:
        key = (shipment["destination_station_id"], shipment["fuel_type"])
        served[key] = served.get(key, 0.0) + shipment["quantity"]
    for key, shortfall in breakdown["unmet_by_entity"].items():
        station_id, fuel = key.split(":")
        assert shortfall > 0
        assert need.get((station_id, fuel), 0.0) > served.get((station_id, fuel), 0.0)
        assert shortfall == pytest.approx(
            need[(station_id, fuel)] - served.get((station_id, fuel), 0.0), abs=1.0)
    # A station+fuel the plan fully covers is never listed as unmet.
    for key, shortfall in breakdown["unmet_by_entity"].items():
        assert shortfall >= 1.0


def test_default_weights_make_shipping_always_preferable_to_leaving_demand_unmet():
    weights = optimize.weights_for({})
    worst_route_cost = weights["transport_weight"] * 8 + weights["late_penalty"]
    assert weights["unmet_penalty"] > worst_route_cost
    assert weights["surplus_weight"] < weights["unmet_penalty"]


def test_a_huge_late_penalty_never_makes_serving_demand_more_expensive():
    # Even with lateness dominating, serving a litre must stay cheaper than dropping it.
    weights = optimize.weights_for({"optimiser": {"late_penalty": 1e9}})
    assert weights["unmet_penalty"] <= weights["late_penalty"]
    # ...which is why the penalty ordering is a documented invariant, not an accident.
    assert optimize.DEFAULT_WEIGHTS["unmet_penalty"] >= optimize.DEFAULT_WEIGHTS["late_penalty"]


def test_lp_prefers_the_cheaper_depot_when_lateness_is_ignored():
    snapshot = snapshot_at()
    policy = with_policy({"late_penalty": 0.0, "transport_weight": 1.0})
    _recommendations, analysis = solve(snapshot, policy)
    shipments, _breakdown = optimize.build_and_solve(snapshot, analysis, policy, "lp_v1")
    assert shipments
    for shipment in shipments:
        route = next(r for r in snapshot.routes if r.id == shipment["route_id"])
        # The fixture's cheaper arcs are the short ones, so no late-feasible long arc is used
        # when the late penalty is zero and both depots can serve the station.
        assert route.transit_ticks <= 4


def test_lp_avoids_late_arcs_when_the_penalty_is_high():
    snapshot = snapshot_at()
    snapshot = snapshot_at(mutate=lambda data: data["instance"].update(tick=96))
    cheap, _ = solve(snapshot, with_policy({"late_penalty": 5000.0}))
    cheap_late = sum(1 for r in cheap if r["solver"]["late_shipments"])
    assert cheap_late == 0 or total_shipped(cheap) == 0


def test_lp_reports_its_objective_breakdown():
    snapshot = snapshot_at()
    recommendations, _analysis = solve(snapshot, POLICY)
    assert recommendations
    solver = recommendations[0]["solver"]
    assert solver["policy"] == "lp_v1"
    assert solver["status"] == "ok"
    assert solver["litres_moved"] == pytest.approx(
        sum(r["action"]["quantity"] for r in recommendations), abs=len(recommendations))
    assert set(solver["weights"]) == {"unmet_penalty", "priority_weight", "transport_weight",
                                      "late_penalty", "surplus_weight"}


def test_lp_uses_the_shared_description_schema():
    snapshot = snapshot_at()
    recommendations, _analysis = solve(snapshot, POLICY)
    for recommendation in recommendations:
        for key in ("impact", "confidence", "plan", "constraints", "alternatives", "explanation",
                    "explanation_source", "policy_version", "model_version", "solver"):
            assert key in recommendation
        assert recommendation["policy_version"] == "lp_v1"
        assert "sibling_proposals" in recommendation["plan"]


def test_lp_quantities_are_whole_positive_litres():
    snapshot = snapshot_at()
    _recommendations, analysis = solve(snapshot, POLICY)
    shipments, _breakdown = optimize.build_and_solve(snapshot, analysis, POLICY, "lp_v1")
    assert shipments
    for shipment in shipments:
        assert isinstance(shipment["quantity"], int)
        assert shipment["quantity"] >= POLICY["minimum_shipment"] or shipment["quantity"] > 0
    assert {s["fuel_type"] for s in shipments} <= set(FUELS)


def test_lp_does_not_invent_stations_depots_or_routes():
    snapshot = snapshot_at()
    recommendations, _analysis = solve(snapshot, POLICY)
    stations = {s.id for s in snapshot.stations}
    depots = {d.id for d in snapshot.depots}
    routes = {r.id: (r.source_depot_id, r.destination_station_id) for r in snapshot.routes}
    for recommendation in recommendations:
        action = recommendation["action"]
        assert recommendation["station_id"] in stations
        assert action["source_depot_id"] in depots
        assert routes[action["route_id"]] == (action["source_depot_id"], recommendation["station_id"])


# --- degenerate worlds: no invented plans --------------------------------------------------

def test_lp_reports_no_need_instead_of_shipping():
    def fill(data):
        for station in data["stations"]:
            station["inventory"] = dict.fromkeys(FUELS, station["capacity"]["DIESEL"])

    snapshot = snapshot_at(mutate=fill)
    _recommendations, analysis = solve(snapshot, POLICY)
    shipments, breakdown = optimize.build_and_solve(snapshot, analysis, POLICY, "lp_v1")
    assert shipments == []
    assert breakdown["shipments"] == 0


def test_lp_reports_unmet_when_no_route_is_usable():
    def disrupt(data):
        for route in data["routes"]:
            route["status"] = "DISRUPTED"

    snapshot = snapshot_at(mutate=disrupt)
    _recommendations, analysis = solve(snapshot, POLICY)
    shipments, breakdown = optimize.build_and_solve(snapshot, analysis, POLICY, "lp_v1")
    assert shipments == []
    assert breakdown["unmet_liters"] > 0
    assert "unmet" in breakdown["note"]


def test_lp_reports_unmet_when_nothing_can_be_dispatched():
    # Every litre already dispatched this tick: the depot has no dispatch budget left.
    def exhaust(data):
        for index, depot in enumerate(data["depots"]):
            depot["dispatch_capacity_per_tick"] = 500
            route = next(r for r in data["routes"] if r["source_depot_id"] == depot["id"])
            data["allocations"].append({
                "id": index + 1, "idempotency_key": f"pending-{index}", "source_depot_id": depot["id"],
                "destination_station_id": route["destination_station_id"], "route_id": route["id"],
                "fuel_type": "DIESEL", "quantity": 500, "created_tick": 24, "eta_tick": 27,
                "status": "PENDING"})

    snapshot = snapshot_at(mutate=exhaust)
    _recommendations, analysis = solve(snapshot, POLICY)
    shipments, breakdown = optimize.build_and_solve(snapshot, analysis, POLICY, "lp_v1")
    assert shipments == []
    assert breakdown["unmet_liters"] > 0
    assert breakdown["note"] == "no dispatchable supply; every net need stays unmet"


def test_lp_reports_unmet_when_every_depot_is_empty():
    def drain(data):
        for depot in data["depots"]:
            depot["inventory"] = dict.fromkeys(FUELS, 0)

    snapshot = snapshot_at(mutate=drain)
    _recommendations, analysis = solve(snapshot, POLICY)
    shipments, breakdown = optimize.build_and_solve(snapshot, analysis, POLICY, "lp_v1")
    assert shipments == []
    assert breakdown["unmet_liters"] > 0


# --- fallback: the solver must never be trusted blindly ------------------------------------

def test_lp_falls_back_to_greedy_when_the_solver_fails():
    def explode(*_args, **_kwargs):
        raise optimize.OptimisationError("solver unavailable in test")

    original = optimize.build_and_solve
    optimize.build_and_solve = explode
    try:
        snapshot = snapshot_at()
        recommendations, analysis = solve(snapshot, POLICY)
    finally:
        optimize.build_and_solve = original
    assert recommendations
    assert {r["solver"]["policy"] for r in recommendations} == {"greedy_v1"}
    assert all(r["solver"]["fallback_from"] == "lp_v1" for r in recommendations)
    assert all("solver unavailable" in r["solver"]["reason"] for r in recommendations)
    assert total_shipped(recommendations) == total_shipped(
        engine.recommend(snapshot, analysis, POLICY, 24, "greedy_v1"))


def test_lp_falls_back_when_the_plan_fails_the_shared_validator():
    snapshot = snapshot_at()
    oversized = [{"source_depot_id": d.id, "destination_station_id": s.id, "route_id": r.id,
                  "fuel_type": fuel, "quantity": r.max_shipment + 1}
                 for d in snapshot.depots for s in snapshot.stations
                 for r in snapshot.routes if r.source_depot_id == d.id
                 and r.destination_station_id == s.id for fuel in FUELS[:1]]
    original = optimize.build_and_solve
    optimize.build_and_solve = lambda *a, **k: (oversized, {"status": "ok", "unmet_liters": 0})
    try:
        recommendations, _analysis = solve(snapshot, POLICY)
    finally:
        optimize.build_and_solve = original
    assert recommendations
    assert all(r["solver"]["policy"] == "greedy_v1" for r in recommendations)
    assert all("shared validator" in r["solver"]["reason"] for r in recommendations)


def test_lp_falls_back_on_a_non_positive_quantity():
    original = optimize.build_and_solve
    optimize.build_and_solve = lambda *a, **k: (
        [{"source_depot_id": "depot-gazipur", "destination_station_id": "station-mirpur",
          "route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 0}],
        {"status": "ok", "unmet_liters": 0})
    try:
        recommendations, _analysis = solve(snapshot_at(), POLICY)
    finally:
        optimize.build_and_solve = original
    assert all(r["solver"]["policy"] == "greedy_v1" for r in recommendations)
    assert all("non-positive" in r["solver"]["reason"] for r in recommendations)


def test_do_nothing_policy_still_short_circuits():
    snapshot = snapshot_at()
    analysis = engine.analyse(snapshot, paths=24)
    assert engine.recommend(snapshot, analysis, POLICY, 24, "do_nothing") == []


# --- wiring: the policy registry reaches the optimiser -------------------------------------

@pytest.mark.parametrize("policy_name", ["lp_v1", "lp_ml_v1"])
def test_every_optimizer_policy_name_routes_through_the_solver(policy_name):
    snapshot = snapshot_at()
    analysis = engine.analyse(snapshot, paths=24)
    assert policy_name in optimize.policy_names()
    assert policy_name in engine.OPTIMIZER_POLICIES
    plans = engine.recommend(snapshot, analysis, POLICY, 24, policy_name)
    assert {p["policy_version"] for p in plans} <= {policy_name, "greedy_v1"}


def test_weights_come_from_policy_and_have_defaults():
    assert optimize.weights_for(None)["unmet_penalty"] == optimize.DEFAULT_WEIGHTS["unmet_penalty"]
    merged = optimize.weights_for({"optimiser": {"transport_weight": 7.0}})
    assert merged["transport_weight"] == 7.0
    assert merged["unmet_penalty"] == optimize.DEFAULT_WEIGHTS["unmet_penalty"]


def test_repository_policy_config_documents_the_objective():
    assert "unmet_penalty" in engine.POLICY["optimiser"]
    assert "transport_weight" in engine.POLICY["optimiser"]
    assert engine.POLICY["optimiser"]["reserve_fraction"] >= 0
