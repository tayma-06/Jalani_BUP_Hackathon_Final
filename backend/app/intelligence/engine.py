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


def event_applies(event, station=None, depot=None, route=None):
    p = event.parameters
    checks = []
    if station:
        checks += [("station_ids", station.id), ("region_ids", station.region_id)]
    if depot:
        checks += [("depot_ids", depot.id)]
    if route:
        checks += [("route_ids", route.id)]
    return all(not p.get(key) or value in p[key] for key, value in checks)


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


def forecast(snapshot, station, fuel, hours=24):
    tick_minutes = snapshot.instance.tick_minutes
    count = max(1, int(hours * 60 / tick_minutes))
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


def analyse(snapshot: Snapshot, paths=300, horizon_hours=12):
    result = {}
    horizon = max(1, int(horizon_hours * 60 / snapshot.instance.tick_minutes))
    for station in snapshot.stations:
        for fuel in FUELS:
            prediction = forecast(snapshot, station, fuel)
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

                def whatif(q, arrival=eta):
                    return project(station.inventory[fuel], station.capacity[fuel], a["mean"][:length],
                                   a["std"][:length], a["inbound"] + extra_arrivals + [(arrival, q)],
                                   snapshot.instance.tick, snapshot.instance.tick_minutes,
                                   seed_for(snapshot, station.id, fuel), paths)

                before, after, half = whatif(0), whatif(quantity), whatif(quantity / 2)
                crisis = any(e.status == "ACTIVE" and event_applies(e, station, depot, route) for e in snapshot.events)
                score = max(0, 0.8 - 0.2 * crisis - 0.1 * cross_region - 0.1 * rationing)
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
                recommendation = {
                    "created_tick": snapshot.instance.tick, "station_id": station.id, "fuel_type": fuel,
                    "current_inventory": station.inventory[fuel], "projected_stockout_hours": a["hours_to_stockout"],
                    "expected_demand_12h": a["forecast_12h"],
                    "action": {"source_depot_id": depot.id, "route_id": route.id, "quantity": quantity, "eta_tick": eta},
                    "impact": {"risk_before": before["risk"], "risk_after": after["risk"],
                               "unmet_before_l": before["expected_unmet_liters"], "unmet_after_l": after["expected_unmet_liters"]},
                    "confidence": {"score": round(score, 2), "level": "HIGH" if score >= 0.75 else "MEDIUM" if score >= 0.5 else "LOW"},
                    "requires_review": crisis or cross_region or rationing or score < 0.75,
                    "rationing": rationing,
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


def detect(snapshot, analysis, previous=None):
    alerts = []

    def add(kind, entity, message, severity="WARNING", fuel=None):
        alerts.append({"key": f"{kind}:{entity}:{fuel or '-'}", "type": kind, "entity_id": entity,
                       "fuel_type": fuel, "severity": severity, "message": message,
                       "last_tick": snapshot.instance.tick,
                       "event_ids": [e.id for e in snapshot.events if e.status == "ACTIVE"]})

    old_supply = {s.id: s for s in previous.supply} if previous else {}
    old_stations = {s.id: s for s in previous.stations} if previous else {}
    for station in snapshot.stations:
        if station.status == "OUTAGE":
            add("station_outage", station.id, f"{station.name} is unavailable; allocations paused.", "CRITICAL")
        old = old_stations.get(station.id)
        if station.demand_multiplier > 1.2 or (old and abs(old.demand_multiplier - station.demand_multiplier) > 0.1):
            add("demand_anomaly", station.id, f"{station.name}: demand multiplier {station.demand_multiplier:.2f}.")
        for fuel in FUELS:
            a = analysis[(station.id, fuel)]
            if a["risk"] >= 0.5:
                add("shortage", station.id, f"{station.name} {fuel}: {a['risk']:.0%} estimated stockout risk.", a["risk_level"], fuel)
            if not a["known"]:
                add("unknown_profile", station.id, "Unknown demand profile with no history; forecasting requires review.", "WARNING", fuel)
    for route in snapshot.routes:
        if route.status == "DISRUPTED":
            add("route_disruption", route.id, f"Route {route.id} is disrupted; use available alternatives.")
    for supply in snapshot.supply:
        if supply.status == "DELAYED" or (supply.status != "ARRIVED" and supply.planned_tick < snapshot.instance.tick):
            add("supply_delay", supply.id, f"Incoming {supply.fuel_type.lower()} delivery is delayed.")
        old = old_supply.get(supply.id)
        if old and supply.quantity < old.quantity - 0.01:
            add("supply_shortfall", supply.id, f"Delivery reduced from {old.quantity:,.0f} to {supply.quantity:,.0f} L.")
    for depot in snapshot.depots:
        used = sum(a.quantity for a in snapshot.allocations if a.source_depot_id == depot.id and a.status in ACTIVE)
        if depot.status == "CONSTRAINED" or used > depot.dispatch_capacity_per_tick * 0.8:
            add("depot_bottleneck", depot.id, f"{depot.name}: {depot.status.lower()}, {used:,.0f} L dispatch committed.")
    for region in snapshot.regions:
        high = sum(analysis[(s.id, f)]["risk"] >= 0.5 for s in snapshot.stations if s.region_id == region.id for f in FUELS)
        if high >= 2:
            add("regional_shortage", region.id, f"{region.name}: {high} station/fuel combinations have high shortage risk.", "HIGH")
    return alerts
