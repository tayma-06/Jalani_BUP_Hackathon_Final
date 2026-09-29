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
# Identical, pre-scheduled disruptions for every policy (ticks are 15 simulated minutes).
SCENARIOS = {
    "baseline": [],
    "stress": [
        {"type": "supply_shortfall", "start_tick": 1, "duration_ticks": 287,
         "parameters": {"depot_ids": ["depot-gazipur"], "factor": 0.5}},
        {"type": "demand_spike", "start_tick": 24, "duration_ticks": 72,
         "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8}},
        {"type": "route_disruption", "start_tick": 48, "duration_ticks": 48,
         "parameters": {"route_ids": ["route-gazipur-mirpur"]}},
        {"type": "demand_spike", "start_tick": 120, "duration_ticks": 48,
         "parameters": {"region_ids": ["region-chattogram"], "multiplier": 1.5}},
    ],
}


def post(base, path, token, body=None, attempts=1):
    """Only pass attempts > 1 for calls that are safe to repeat (refresh, idempotent approval)."""
    for attempt in range(attempts):
        try:
            status, _, data = request(base, path, "POST", body if body is not None else {}, token=token, timeout=60)
            return status, data
        except OSError:
            if attempt + 1 == attempts:
                raise
            time.sleep(1)


def proposals(base, token):
    for attempt in range(3):
        try:
            status, _, recs = request(base, "/api/recommendations?status=PROPOSED", token=token, timeout=60)
            require(status == 200 and isinstance(recs, list), f"Recommendations unreadable: HTTP {status}")
            return recs
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1)


def step_once(base, simulator, admin, expected_tick):
    """Advance exactly one tick; after a dropped connection, check the simulator before retrying."""
    for _ in range(3):
        try:
            status, data = post(base, "/api/control/sim/step", admin)
            require(status == 200, f"step to {expected_tick} failed: HTTP {status} {data}")
            return
        except OSError:
            time.sleep(1)
            _, _, instance = request(simulator, "/v1/instance")
            if instance.get("tick") == expected_tick:
                return
            require(instance.get("tick") == expected_tick - 1, f"Unexpected tick {instance.get('tick')}")
    raise AssertionError(f"Could not step to tick {expected_tick}")


def sim_metrics(simulator):
    status, _, data = request(simulator, "/v1/metrics")
    require(status == 200 and isinstance(data, dict), "Simulator metrics unreadable")
    return data


def run_policy(base, simulator, admin, operator, policy, ticks, scenario="baseline"):
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
    for event in SCENARIOS[scenario]:
        status, data = post(base, "/api/control/events", admin, event)
        require(status == 200, f"Scheduling {event['type']} failed: HTTP {status} {data}")

    approved = rejected_by_gate = shipped_liters = 0
    unmet_curve = []
    started = time.monotonic()
    for tick in range(1, ticks + 1):
        # The control-room step runs a full refresh + decision cycle before it returns.
        step_once(base, simulator, admin, tick)
        recs = proposals(base, operator)
        if policy != "do_nothing":
            for rec in [r for r in recs if r.get("status") == "PROPOSED"]:
                status, result = post(base, f"/api/recommendations/{rec['id']}/approve", operator, attempts=3)
                if status == 200 and isinstance(result, dict) and result.get("sim_id") is not None:
                    approved += 1
                    shipped_liters += rec["action"]["quantity"]
                else:
                    rejected_by_gate += 1
        if tick % 24 == 0 or tick == ticks:
            unmet_curve.append({"tick": tick, "unmet_liters": round(sim_metrics(simulator)["unmet_demand_liters"], 1)})
    final = sim_metrics(simulator)
    return {"policy": policy, "scenario": scenario, "ticks": ticks, "seed": instance.get("seed"),
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
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="baseline")
    parser.add_argument("--allow-reset", action="store_true", required=True)
    parser.add_argument("--output", default="artifacts/benchmark.json")
    args = parser.parse_args()
    admin = login(args.base, os.environ.get("ADMIN_USER", "admin"), os.environ.get("ADMIN_PASSWORD", "demo-admin"))
    operator = login(args.base, os.environ.get("OPERATOR_USER", "operator"),
                     os.environ.get("OPERATOR_PASSWORD", "demo-operator"))
    results = []
    for policy in args.policies.split(","):
        result = run_policy(args.base, args.simulator, admin, operator, policy, args.ticks, args.scenario)
        print(json.dumps({k: v for k, v in result.items() if k != "unmet_curve"}), flush=True)
        results.append(result)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps({"results": results}, indent=2) + "\n")
