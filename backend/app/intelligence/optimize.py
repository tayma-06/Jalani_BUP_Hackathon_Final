"""Constrained allocation optimisation (policy `lp_v1`, hybrid `lp_ml_v1`).

Formulation
-----------
The decision is **how many litres to ship on each feasible route**, not how many shipments
to create, so the problem is a *continuous* linear program over shipment quantities. Its
constraint matrix is a network matrix, so the LP relaxation has integral vertices: the exact
optimum is a min-cost flow and the shipped quantities are whole litres. No mixed-integer
solver is needed, and none is claimed.

Network
    source -> depot:fuel -> gate_in -> gate_out -> station:fuel -> { tank | unmet }
                              (cap = depot dispatch, shared by every fuel)

Objective, per litre (weights come from `config/policy.yaml`, section `optimiser`)
    * unmet demand         : `unmet_penalty` + `priority_weight * fuel weight`
    * transport effort     : `transport_weight * transit_ticks` (on the route arc)
    * late delivery        : `late_penalty` when arrival is after the projected stockout
    * unnecessary movement : `surplus_weight` for litres above the station's net need

Constraints enforced by the network
    depot stock per depot+fuel, one shared dispatch budget per depot, route shipment limit,
    station tank space, delivery timing, home-region reserve, station/depot status
    (a closed or disrupted entity simply gets no arcs).

Every solution is re-checked with the shared `validate_batch` before it is returned. If the
solve fails, times out or returns anything invalid, the caller falls back to `greedy_v1`
and says so in the recommendation's `solver` block.
"""
from __future__ import annotations

import heapq
import math

from app.intelligence.engine import FUELS, budgets, describe_shipment, validate_batch

DEFAULT_WEIGHTS = {
    "unmet_penalty": 1000.0,
    "priority_weight": 0.0,      # scales with the fuel weight to express priority
    "transport_weight": 2.0,
    "late_penalty": 400.0,
    "surplus_weight": 1.0,
    "reserve_fraction": 0.15,
    "horizon_hours": 12,
    "max_arcs": 200000,
}


class OptimisationError(Exception):
    """Any reason the optimiser cannot be trusted for this cycle."""


class Timeout(OptimisationError):
    pass


# --- exact min-cost flow: successive shortest paths with potentials ------------------------

def min_cost_flow(node_count, arcs, required_flow, max_arcs=DEFAULT_WEIGHTS["max_arcs"],
                  source=0, sink=None):
    """Minimum-cost flow of exactly `required_flow`; returns a list of per-arc flows.

    `arcs` is [(tail, head, capacity, cost)] with non-negative costs. Deterministic: equal
    reduced costs break on node id, so the same world always produces the same plan.
    """
    sink = node_count - 1 if sink is None else sink
    if len(arcs) > max_arcs:
        raise OptimisationError(f"model too large: {len(arcs)} arcs exceeds {max_arcs}")
    outgoing = [[] for _ in range(node_count)]
    for index, (tail, head, capacity, cost) in enumerate(arcs):
        if capacity <= 0:
            continue
        outgoing[tail].append((head, index, False, cost))
        outgoing[head].append((tail, index, True, -cost))
    if sum(a[2] for a in arcs if a[0] == source) < required_flow - 1e-6:
        raise OptimisationError("network supply cannot meet the flow the model requests")
    flow = [0.0] * len(arcs)
    potential = [0.0] * node_count
    sent = 0.0
    guard = 0
    while sent < required_flow - 1e-6:
        guard += 1
        if guard > 4 * len(arcs) + 64:
            raise Timeout("min-cost flow did not converge")
        distance = [math.inf] * node_count
        previous = [None] * node_count
        distance[source] = 0.0
        queue = [(0.0, source)]
        while queue:
            cost, node = heapq.heappop(queue)
            if cost > distance[node] + 1e-9:
                continue
            for head, index, reverse, arc_cost in outgoing[node]:
                if reverse:
                    if flow[index] <= 1e-9:
                        continue
                    residual = flow[index]
                else:
                    residual = arcs[index][2] - flow[index]
                    if residual <= 1e-9:
                        continue
                reduced = cost + arc_cost + potential[node] - potential[head]
                if reduced < distance[head] - 1e-9:
                    distance[head] = reduced
                    previous[head] = (node, index, reverse)
                    heapq.heappush(queue, (reduced, head))
        if previous[sink] is None:
            raise OptimisationError("no augmenting path remains; model is infeasible")
        for node, d in enumerate(distance):
            if d < math.inf:
                potential[node] += d
        push = required_flow - sent
        node = sink
        while node != source:
            _prev_node, index, reverse = previous[node]
            capacity = flow[index] if reverse else arcs[index][2] - flow[index]
            push = min(push, capacity)
            node = previous[node][0]
        node = sink
        while node != source:
            prev_node, index, reverse = previous[node]
            flow[index] += -push if reverse else push
            node = prev_node
        sent += push
    return flow


# --- the linear program --------------------------------------------------------------------

def weights_for(policy):
    return {**DEFAULT_WEIGHTS, **((policy or {}).get("optimiser") or {})}


def build_and_solve(snapshot, analysis, policy, policy_name="lp_v1"):
    """Solve the allocation LP. Returns (shipments, breakdown); raises OptimisationError."""
    weights = weights_for(policy)
    stock, dispatch, space = budgets(snapshot, policy)
    depots = {d.id: d for d in snapshot.depots}
    stations = {s.id: s for s in snapshot.stations}
    tick_minutes = snapshot.instance.tick_minutes
    horizon = max(1, int(weights["horizon_hours"] * 60 / tick_minutes))
    fuel_weights = (policy or {}).get("weights", {})

    need = {}
    for station in snapshot.stations:
        if station.status != "OPEN":
            continue
        for fuel in FUELS:
            entry = analysis[(station.id, fuel)]
            if not entry.get("known"):
                continue
            net = sum(entry["mean"][:horizon]) - station.inventory[fuel] \
                - sum(q for _, q in entry["inbound"])
            if net > 0.5:
                need[(station.id, fuel)] = net

    needed_stations = {station_id for station_id, _ in need}
    routes = [r for r in snapshot.routes
              if r.status == "AVAILABLE"
              and r.destination_station_id in needed_stations
              and stations[r.destination_station_id].status == "OPEN"
              and r.source_depot_id in depots
              and depots[r.source_depot_id].status != "CLOSED"]
    routes = [r for r in routes if any((r.destination_station_id, f) in need for f in FUELS)]
    def no_plan(unmet, note):
        """An empty plan that still books the demand it could not serve."""
        return [], {"status": "ok", "policy": policy_name, "shipments": 0,
                    "unmet_liters": round(unmet, 1), "litres_moved": 0.0, "late_shipments": 0,
                    "dropped_below_minimum_l": 0.0, "unmet_by_entity": {}, "note": note,
                    "objective": {"unmet_liters": round(unmet, 1), "litres_moved": 0.0,
                                  "weights": {k: weights[k] for k in
                                              ("unmet_penalty", "priority_weight", "transport_weight",
                                               "late_penalty", "surplus_weight")}},
                    "weights": {k: weights[k] for k in
                                ("unmet_penalty", "priority_weight", "transport_weight",
                                 "late_penalty", "surplus_weight")}}
    if not need:
        return no_plan(0.0, "no station needs fuel")
    if not routes:
        return no_plan(sum(need.values()), "no feasible route; every net need stays unmet")

    nodes, arcs, tags = {}, [], []

    def node(key):
        if key not in nodes:
            nodes[key] = len(nodes)
        return nodes[key]

    source, sink = node("source"), node("sink")
    depot_gate_out = {}
    for depot in snapshot.depots:
        gate_in, gate_out = node(f"gate_in:{depot.id}"), node(f"gate_out:{depot.id}")
        depot_gate_out[depot.id] = (gate_in, gate_out)
        # One shared arc enforces the dispatch budget for every fuel leaving this depot.
        arcs.append((gate_in, gate_out, max(0.0, dispatch[depot.id]), 0.0))
        tags.append(("dispatch", depot.id, None, None))

    for (station_id, fuel), volume in need.items():
        key = f"sf:{station_id}:{fuel}"
        tank = node(key)
        useful = min(volume, max(0.0, space[(station_id, fuel)]))
        surplus = max(0.0, space[(station_id, fuel)] - useful)
        penalty = float(weights["unmet_penalty"]) + float(weights["priority_weight"]) * \
            float(fuel_weights.get(fuel, 1.0))
        arcs.append((tank, sink, useful, 0.0))
        tags.append(("deliver", station_id, fuel, "need"))
        arcs.append((tank, sink, surplus, float(weights["surplus_weight"])))
        tags.append(("deliver", station_id, fuel, "surplus"))
        unmet_node = node(f"unmet:{station_id}:{fuel}")
        arcs.append((tank, unmet_node, volume, penalty))
        tags.append(("unmet", station_id, fuel, None))
        arcs.append((unmet_node, sink, volume, 0.0))
        tags.append(("unmet_sink", station_id, fuel, None))

    reserve_fraction = float(weights["reserve_fraction"])
    for route in routes:
        station = stations[route.destination_station_id]
        depot = depots[route.source_depot_id]
        gate_out = depot_gate_out[route.source_depot_id][1]
        cross_region = depot.region_id != station.region_id
        for fuel in FUELS:
            if (station.id, fuel) not in need:
                continue
            reserve = 0.0
            if cross_region:
                reserve = reserve_fraction * sum(
                    need.get((s.id, fuel), 0.0) for s in snapshot.stations
                    if s.region_id == depot.region_id)
            available = max(0.0, stock[(depot.id, fuel)] - reserve)
            depot_fuel = node(f"df:{depot.id}:{fuel}")
            arcs.append((source, depot_fuel, available, 0.0))
            tags.append(("stock", depot.id, fuel, None))
            arcs.append((depot_fuel, depot_gate_out[depot.id][0], available, 0.0))
            tags.append(("gate", depot.id, fuel, None))
            late = 0.0
            stockout = analysis[(station.id, fuel)].get("hours_to_stockout")
            if stockout is not None and (route.transit_ticks + 1) * tick_minutes / 60 > stockout:
                late = float(weights["late_penalty"])
            arcs.append((gate_out, node(f"sf:{station.id}:{fuel}"), route.max_shipment,
                         float(weights["transport_weight"]) * route.transit_ticks + late))
            tags.append(("route", route.id, station.id, fuel))

    total_need = sum(need.values())
    total_supply = sum(arc[2] for index, arc in enumerate(arcs) if tags[index][0] == "stock")
    total_dispatch = sum(arc[2] for index, arc in enumerate(arcs) if tags[index][0] == "dispatch")
    total_supply = min(total_supply, total_dispatch)
    if total_supply <= 0.5:
        return no_plan(total_need, "no dispatchable supply; every net need stays unmet")

    flows = min_cost_flow(len(nodes), arcs, min(total_need, total_supply),
                          int(weights["max_arcs"]), source=source, sink=sink)

    # Rounding a continuous solution to whole litres can push a depot one litre past a
    # shared budget, so round *down* and re-check every cap here rather than trusting it.
    minimum = float((policy or {}).get("minimum_shipment", 0.0) or 0.0)
    stock_left, dispatch_left, space_left = dict(stock), dict(dispatch), dict(space)
    by_id = {r.id: r for r in routes}
    shipments, moved, late, dropped = [], 0.0, 0, 0.0
    for index, tag in enumerate(tags):
        quantity = flows[index] if flows[index] > 1e-6 else 0.0
        if not quantity:
            continue
        if tag[0] == "route":
            route = by_id[tag[1]]
            whole = int(math.floor(quantity))
            limit = min(route.max_shipment, stock_left[(route.source_depot_id, tag[3])],
                        dispatch_left[route.source_depot_id], space_left[(tag[2], tag[3])])
            whole = min(whole, int(math.floor(limit)))
            if whole < minimum:
                dropped += quantity
                continue
            if whole <= 0:
                continue
            shipments.append({"source_depot_id": route.source_depot_id,
                              "destination_station_id": tag[2], "route_id": route.id,
                              "fuel_type": tag[3], "quantity": whole})
            stock_left[(route.source_depot_id, tag[3])] -= whole
            dispatch_left[route.source_depot_id] -= whole
            space_left[(tag[2], tag[3])] -= whole
            moved += whole
            stockout = analysis[(tag[2], tag[3])].get("hours_to_stockout")
            if stockout is not None and \
                    (route.transit_ticks + 1) * tick_minutes / 60 > stockout:
                late += 1

    # Report the true shortfall, not just the flow that reached the unmet sink: demand can
    # also be unserved because the depot ran dry, the route was full, or the tank had no room.
    served = {}
    for shipment in shipments:
        key = (shipment["destination_station_id"], shipment["fuel_type"])
        served[key] = served.get(key, 0.0) + shipment["quantity"]
    unmet_total = 0.0
    unmet_by_entity = {}
    for key, volume in need.items():
        shortfall = volume - served.get(key, 0.0)
        # Sub-litre residue is whole-litre rounding, not a real service failure: reporting it
        # as an unmet entity would make the shortfall look like a physical shortfall.
        if shortfall >= 1.0:
            unmet_total += shortfall
            unmet_by_entity[f"{key[0]}:{key[1]}"] = round(shortfall, 1)
    objective = {"unmet_liters": round(unmet_total, 1), "litres_moved": round(moved, 1),
                 "weights": {k: weights[k] for k in
                             ("unmet_penalty", "priority_weight", "transport_weight",
                              "late_penalty", "surplus_weight")}}
    return shipments, {
        "status": "ok", "policy": policy_name, "shipments": len(shipments),
        "unmet_liters": round(unmet_total, 1), "litres_moved": round(moved, 1),
        "late_shipments": late, "dropped_below_minimum_l": round(dropped, 1),
        "unmet_by_entity": unmet_by_entity, "objective": objective,
        "weights": objective["weights"],
    }


def optimize_recommend(snapshot, analysis, policy, paths=300, policy_name="lp_v1"):
    """The LP plan in the shared recommendation schema, or a labelled greedy fallback."""
    from app.intelligence.engine import recommend as greedy_recommend

    def fallback(reason):
        plans = greedy_recommend(snapshot, analysis, policy, paths, "greedy_v1")
        for plan in plans:
            plan["solver"] = {"policy": "greedy_v1", "fallback_from": policy_name, "reason": reason}
        return plans

    try:
        shipments, breakdown = build_and_solve(snapshot, analysis, policy, policy_name)
    except OptimisationError as exc:
        return fallback(f"optimiser did not run: {exc}")
    if any(shipment["quantity"] <= 0 for shipment in shipments):
        return fallback("optimiser returned a non-positive quantity")
    bodies = [{"idempotency_key": "lp-plan", **shipment} for shipment in shipments]
    violation = validate_batch(snapshot, bodies, policy) if bodies else None
    if violation:
        return fallback(f"plan failed the shared validator: {violation}")

    depots = {d.id: d for d in snapshot.depots}
    routes_by_station = {}
    for route in snapshot.routes:
        routes_by_station.setdefault(route.destination_station_id, []).append(route)
    grouped = {}
    for shipment in shipments:
        grouped.setdefault((shipment["destination_station_id"], shipment["fuel_type"]), []).append(shipment)
    planned = []
    for (station_id, fuel), entries in grouped.items():
        station = stations_for(snapshot, station_id)
        arrivals = []
        for shipment in entries:
            route = next(r for r in snapshot.routes if r.id == shipment["route_id"])
            arrivals.append((snapshot.instance.tick + 1 + route.transit_ticks, shipment["quantity"]))
        for index, shipment in enumerate(entries):
            route = next(r for r in snapshot.routes if r.id == shipment["route_id"])
            depot = depots[shipment["source_depot_id"]]
            eta = snapshot.instance.tick + 1 + route.transit_ticks
            siblings = [a for position, a in enumerate(arrivals) if position != index]
            planned.append(describe_shipment(
                snapshot, analysis, station, fuel, depot, route, shipment["quantity"], eta,
                siblings=siblings, routes=routes_by_station.get(station_id, []), policy=policy,
                paths=paths, cross_region=depot.region_id != station.region_id,
                rationing=breakdown.get("unmet_liters", 0) > 0,
                target_hours=int(weights_for(policy)["horizon_hours"]),
                policy_name=policy_name, solver_note=breakdown))
    return planned


def stations_for(snapshot, station_id):
    return next(s for s in snapshot.stations if s.id == station_id)


def policy_names():
    return ["lp_v1", "lp_ml_v1"]


__all__ = ["DEFAULT_WEIGHTS", "OptimisationError", "Timeout", "build_and_solve", "min_cost_flow",
           "optimize_recommend", "policy_names", "weights_for"]
