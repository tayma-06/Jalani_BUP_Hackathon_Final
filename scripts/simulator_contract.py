"""Destructive tests: ONLY for an isolated disposable simulator, never staging/live."""
import argparse
import json
import uuid
from pathlib import Path

from http_checks import code, eventually, request, require


def candidate(depots, stations, routes):
    ds = {d["id"]: d for d in depots}
    ss = {s["id"]: s for s in stations}
    for route in routes:
        depot = ds[route["source_depot_id"]]
        station = ss[route["destination_station_id"]]
        if (route["status"] != "AVAILABLE" or station["status"] != "OPEN"
                or depot["status"] not in ("OPEN", "CONSTRAINED")):
            continue
        for fuel in ("DIESEL", "PETROL", "OCTANE"):
            headroom = min(route["max_shipment"], depot["inventory"][fuel],
                           depot["dispatch_capacity_per_tick"],
                           station["capacity"][fuel] - station["inventory"][fuel])
            if headroom >= 2:
                return {"idempotency_key": "ci-" + uuid.uuid4().hex,
                        "source_depot_id": depot["id"],
                        "destination_station_id": station["id"],
                        "route_id": route["id"], "fuel_type": fuel,
                        "quantity": min(100.0, headroom / 2)}
    raise AssertionError("No small feasible shipment found on the reset world")


def run(base):
    def get(path):
        status, _, data = request(base, path)
        require(status == 200, f"{path}: HTTP {status}")
        return data

    eventually(lambda: get("/v1/health"))
    for action in ("pause", "reset", "pause"):
        status, _, _ = request(base, "/admin/" + action, "POST")
        require(status in (200, 201), f"Admin {action}: HTTP {status}")
    instance = get("/v1/instance")
    require(instance["status"] == "PAUSED", "Deterministic test must be paused")
    resources = {name: get("/v1/" + name) for name in
                 ("depots", "stations", "routes", "supply-arrivals", "allocations")}
    payload = candidate(resources["depots"], resources["stations"], resources["routes"])
    first_status, _, first = request(base, "/v1/allocations", "POST", payload)
    require(first_status == 201, f"New allocation: HTTP {first_status}")
    require(first["status"] == "PENDING", "New shipment should be PENDING while paused")
    replay_status, _, replay = request(base, "/v1/allocations", "POST", payload)
    require(replay_status in (200, 201), "Replay must return 200 or 201")
    require(replay["id"] == first["id"], "Replay duplicated the allocation")
    changed = dict(payload, quantity=payload["quantity"] + 1)
    status, _, data = request(base, "/v1/allocations", "POST", changed)
    require(status == 409 and code(data) == "IDEMPOTENCY_KEY_MISMATCH",
            "Changed body under a used key must be rejected")
    status, _, cancelled = request(base, f"/v1/allocations/{first['id']}/cancel", "POST")
    require(status == 200 and cancelled["status"] == "CANCELLED", "Pending cancel failed")
    ledger = get("/v1/allocations")
    require(sum(a["idempotency_key"] == payload["idempotency_key"] for a in ledger) == 1,
            "Expected exactly one ledger entry for one key")
    start_tick = get("/v1/instance")["tick"]
    for _ in range(10):
        status, _, _ = request(base, "/admin/step", "POST")
        require(status == 200, "Deterministic step failed")
    final_tick = get("/v1/instance")["tick"]
    require(final_tick == start_tick + 10, "Clock advanced unexpectedly")
    history = get("/v1/demand-history?limit=2000")
    require(len(history) > 0, "No demand history after stepping")
    return {"status": "passed", "source": "real HTTP target; confirm official image in CI logs",
            "replay_http_status": replay_status, "same_allocation_id": True,
            "changed_body_rejected": True, "pending_cancel": True,
            "station_count": len(resources["stations"]),
            "supply_arrival_count": len(resources["supply-arrivals"]),
            "last_planned_supply_tick": max((s["planned_tick"] for s in resources["supply-arrivals"]), default=None),
            "final_tick": final_tick, "history_rows": len(history),
            "metrics": get("/v1/metrics")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator", default="http://127.0.0.1:18000")
    parser.add_argument("--allow-reset", action="store_true", required=True,
                        help="Explicitly authorize resetting this DISPOSABLE test simulator")
    parser.add_argument("--output", default="artifacts/simulator-contract.json")
    args = parser.parse_args()
    result = run(args.simulator)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
