"""Destructive end-to-end drill: ONLY for a local/disposable stack. Resets the simulator.

Drives the real product flow through the frontend proxy against the official simulator:
role enforcement, over-limit rejection, concurrent duplicate approval, shipment delivery,
route disruption and demand spike detection. Complements app_smoke.py and fault_probe.py.
"""
import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from http_checks import eventually, login, request, require


def sim_allocations(simulator, key):
    status, _, rows = request(simulator, "/v1/allocations")
    require(status == 200 and isinstance(rows, list), "Simulator allocations unreadable")
    return [a for a in rows if a.get("idempotency_key") == key]


def state(base, token):
    status, _, data = request(base, "/api/network/state", token=token)
    require(status == 200 and isinstance(data, dict), f"Network state HTTP {status}")
    return data


def control(base, admin, action, body=None):
    status, _, data = request(base, f"/api/control/sim/{action}", "POST", body, token=admin)
    require(status == 200, f"sim {action} returned HTTP {status}: {data}")


def step(base, admin, ticks):
    for _ in range(ticks):
        control(base, admin, "step")


def drill(base, simulator, creds):
    admin = login(base, *creds["admin"])
    operator = login(base, *creds["operator"])
    viewer = login(base, *creds["viewer"])
    evidence = {}

    # Fresh, paused world so every run is repeatable.
    control(base, admin, "reset", {"confirm": True})
    control(base, admin, "pause")
    eventually(lambda: require(state(base, operator)["tick"] == 0, "Backend has not seen the reset"), timeout=30)

    def proposals():
        step(base, admin, 4)
        status, _, recs = request(base, "/api/recommendations", token=operator)
        require(status == 200 and recs, "No proposals yet")
        return recs
    recs = eventually(proposals, timeout=120, interval=0)
    rec = max(recs, key=lambda r: r["impact"]["risk_before"])
    qty = rec["action"]["quantity"]
    evidence["proposal"] = {"tick": state(base, operator)["tick"], "count": len(recs), "station": rec["station_id"],
                            "fuel": rec["fuel_type"], "quantity": qty, "route": rec["action"]["route_id"],
                            "risk_before": rec["impact"]["risk_before"], "risk_after": rec["impact"]["risk_after"]}

    # Role enforcement lives in the backend, not only the UI.
    status, _, _ = request(base, f"/api/recommendations/{rec['id']}/approve", "POST", {}, token=viewer)
    require(status == 403, f"Viewer approval must be 403, got {status}")
    status, _, _ = request(base, "/api/control/sim/step", "POST", token=operator)
    require(status == 403, f"Operator control must be 403, got {status}")
    evidence["roles"] = "viewer approve 403, operator control 403"

    # Quantity may only shrink the reviewed proposal.
    status, _, _ = request(base, f"/api/recommendations/{rec['id']}/approve", "POST", {"quantity": qty + 1}, token=operator)
    require(status == 422, f"Over-limit approval must be 422, got {status}")
    evidence["over_limit"] = "quantity+1 rejected with 422"

    # Two simultaneous approvals must produce one simulator allocation.
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: request(base, f"/api/recommendations/{rec['id']}/approve", "POST", {},
                                                   token=operator), range(2)))
    ok = [data for status, _, data in results if status == 200]
    require(ok, f"No approval succeeded: {[s for s, _, _ in results]}")
    require(len({d["id"] for d in ok}) == 1, "Concurrent approvals created two executions")
    key = ok[0]["request"]["idempotency_key"]
    retry_status, _, retry = request(base, f"/api/recommendations/{rec['id']}/approve", "POST", {}, token=operator)
    require(retry_status == 200 and retry["id"] == ok[0]["id"], "Retry must return the same execution")
    rows = sim_allocations(simulator, key)
    require(len(rows) == 1, f"Expected exactly one simulator allocation, found {len(rows)}")
    evidence["duplicate_approval"] = {"http": [s for s, _, _ in results], "executions": 1, "simulator_allocations": 1,
                                      "idempotency_key": key}

    # The accepted shipment travels and arrives in the simulator.
    seen = []

    def delivered():
        step(base, admin, 1)
        current = sim_allocations(simulator, key)[0]["status"]
        if not seen or seen[-1] != current:
            seen.append(current)
        require(current not in {"PENDING", "IN_TRANSIT"}, f"Shipment still {current}")
        return current
    final = eventually(delivered, timeout=120, interval=0)
    status, _, decisions = request(base, "/api/decisions", token=operator)
    require(status == 200 and any(d["request"]["idempotency_key"] == key for d in decisions), "Decision not in history")
    evidence["shipment"] = {"status_sequence": seen, "final": final, "in_decision_history": True}

    # Route disruption: visible in state and no new proposal uses the route.
    route = rec["action"]["route_id"]
    tick = state(base, operator)["tick"]
    status, _, _ = request(base, "/api/control/events", "POST", {"type": "route_disruption", "start_tick": tick + 1,
                           "duration_ticks": 40, "parameters": {"route_ids": [route]}}, token=admin)
    require(status == 200, f"Route disruption injection HTTP {status}")

    def disrupted():
        step(base, admin, 1)
        routes = {r["id"]: r["status"] for r in state(base, operator)["routes"]}
        require(routes.get(route) != "AVAILABLE", f"{route} still available")
        return routes[route]
    evidence["route_disruption"] = {"route": route, "status": eventually(disrupted, timeout=60, interval=0)}
    _, _, recs = request(base, "/api/recommendations", token=operator)
    require(all(r["action"]["route_id"] != route for r in recs), "A proposal uses the disrupted route")
    evidence["route_disruption"]["proposals_avoiding_route"] = len(recs)

    # Demand spike: the system raises alerts without being told where to look.
    before = {a["id"] for a in request(base, "/api/alerts", token=operator)[2]}
    tick = state(base, operator)["tick"]
    status, _, _ = request(base, "/api/control/events", "POST", {"type": "demand_spike", "start_tick": tick + 1,
                           "duration_ticks": 24, "parameters": {"region_ids": ["region-dhaka"], "multiplier": 2.5}},
                           token=admin)
    require(status == 200, f"Demand spike injection HTTP {status}")

    def new_alerts():
        step(base, admin, 1)
        alerts = [a for a in request(base, "/api/alerts", token=operator)[2] if a["id"] not in before]
        require(alerts, "No new alerts yet")
        return alerts
    alerts = eventually(new_alerts, timeout=90, interval=0)
    evidence["demand_spike"] = {"new_alerts": len(alerts), "types": sorted({a["type"] for a in alerts})}

    final_state = state(base, operator)
    evidence["final"] = {"tick": final_state["tick"], "mode": final_state["mode"],
                         "service_level": final_state["kpis"]["service_level"]}
    control(base, admin, "pause")
    return {"status": "passed", **evidence}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:3000")
    parser.add_argument("--simulator", default="http://127.0.0.1:8000")
    parser.add_argument("--allow-reset", action="store_true", required=True,
                        help="Acknowledge that this resets the simulator")
    parser.add_argument("--output", default="artifacts/e2e-drill.json")
    args = parser.parse_args()
    env = os.environ.get
    creds = {role: (env(f"{role.upper()}_USER", role), env(f"{role.upper()}_PASSWORD", f"demo-{role}"))
             for role in ("admin", "operator", "viewer")}
    started = time.monotonic()
    result = drill(args.base, args.simulator, creds)
    result["duration_seconds"] = round(time.monotonic() - started, 1)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
