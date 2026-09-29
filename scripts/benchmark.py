"""Destructive policy benchmark: ONLY for an isolated simulator + backend. Resets the simulator.

Runs each policy over the same seeded world for the same number of ticks, stepping the paused
simulator one tick at a time. After every tick the backend is asked for a fresh decision cycle
and, for the active policies, the operator approves every proposal (full policy adherence).
Scores come from the simulator's own /v1/metrics, not from our model.
"""
import argparse
import json
import os
import time
from pathlib import Path

from http_checks import login, request, require

POLICIES = ("do_nothing", "naive_reorder", "greedy_v1")


def post(base, path, token, body=None):
    status, _, data = request(base, path, "POST", body if body is not None else {}, token=token, timeout=60)
    return status, data


def sim_metrics(simulator):
    status, _, data = request(simulator, "/v1/metrics")
    require(status == 200 and isinstance(data, dict), "Simulator metrics unreadable")
    return data


def run_policy(base, simulator, admin, operator, policy, ticks):
    status, data = post(base, "/api/control/sim/reset", admin, {"confirm": True})
    require(status == 200, f"reset failed: HTTP {status} {data}")
    post(base, "/api/control/sim/pause", admin)
    status, data = post(base, "/api/settings/autopilot", admin, {"mode": "manual"})
    require(status == 200, f"autopilot setting failed: {data}")
    status, data = post(base, "/api/settings/policy", admin,
                        {"version": "greedy_v1" if policy == "do_nothing" else policy})
    require(status == 200, f"policy setting failed: {data}")
    _, _, instance = request(simulator, "/v1/instance")
    require(instance.get("tick") == 0, "Simulator did not reset to tick 0")

    approved = rejected_by_gate = shipped_liters = 0
    unmet_curve = []
    started = time.monotonic()
    for tick in range(1, ticks + 1):
        status, data = post(base, "/api/control/sim/step", admin)
        require(status == 200, f"step {tick} failed: HTTP {status} {data}")
        status, recs = post(base, "/api/decide?dry_run=false", operator)
        require(status == 200 and isinstance(recs, list), f"decision cycle failed at tick {tick}: {recs}")
        if policy != "do_nothing":
            for rec in [r for r in recs if r.get("status") == "PROPOSED"]:
                status, result = post(base, f"/api/recommendations/{rec['id']}/approve", operator)
                if status == 200 and isinstance(result, dict) and result.get("sim_id") is not None:
                    approved += 1
                    shipped_liters += rec["action"]["quantity"]
                else:
                    rejected_by_gate += 1
        if tick % 24 == 0 or tick == ticks:
            unmet_curve.append({"tick": tick, "unmet_liters": round(sim_metrics(simulator)["unmet_demand_liters"], 1)})
    final = sim_metrics(simulator)
    return {"policy": policy, "ticks": ticks, "seed": instance.get("seed"),
            "service_level": round(final["service_level"], 4),
            "served_liters": round(final["served_demand_liters"], 1),
            "unmet_liters": round(final["unmet_demand_liters"], 1),
            "allocation_liters": round(final["allocation_liters"], 1),
            "allocation_failures": final["allocation_failures"],
            "approved_shipments": approved, "approvals_blocked": rejected_by_gate,
            "approved_liters": round(shipped_liters, 1), "unmet_curve": unmet_curve,
            "wall_seconds": round(time.monotonic() - started, 1)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Backend URL of an ISOLATED stack")
    parser.add_argument("--simulator", required=True, help="Simulator URL of the same ISOLATED stack")
    parser.add_argument("--ticks", type=int, default=192, help="Ticks per policy (96 ticks = 1 sim day)")
    parser.add_argument("--policies", default=",".join(POLICIES))
    parser.add_argument("--allow-reset", action="store_true", required=True)
    parser.add_argument("--output", default="artifacts/benchmark.json")
    args = parser.parse_args()
    admin = login(args.base, os.environ.get("ADMIN_USER", "admin"), os.environ.get("ADMIN_PASSWORD", "demo-admin"))
    operator = login(args.base, os.environ.get("OPERATOR_USER", "operator"),
                     os.environ.get("OPERATOR_PASSWORD", "demo-operator"))
    results = []
    for policy in args.policies.split(","):
        result = run_policy(args.base, args.simulator, admin, operator, policy, args.ticks)
        print(json.dumps({k: v for k, v in result.items() if k != "unmet_curve"}), flush=True)
        results.append(result)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps({"results": results}, indent=2) + "\n")
