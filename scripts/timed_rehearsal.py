"""Read-only preflight by default; --allow-reset runs a timed local simulator rehearsal.

The rehearsal resets the selected simulator, switches to manual approval, injects a demand
spike, approves one proposal through the operator API, observes arrival, injects a temporary
upstream failure, and checks recovery. Use only a disposable/local demo stack. It finishes
paused in manual mode and preserves its evidence in the application audit trail.
"""
import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app_smoke import smoke
from http_checks import TokenCache, eventually, request, require


def run(args, evidence):
    started = time.monotonic()
    tokens = TokenCache()
    operator_user = os.environ.get("OPERATOR_USER", "operator")
    operator_password = os.environ.get("OPERATOR_PASSWORD", "demo-operator")
    admin_user = os.environ.get("ADMIN_USER", "admin")
    admin_password = os.environ.get("ADMIN_PASSWORD", "demo-admin")

    def record(name, details):
        elapsed = round(time.monotonic() - started, 3)
        evidence["stages"].append({"stage": name, "elapsed_seconds": elapsed, **details})
        print(f"[{elapsed:6.1f}s] {name}", flush=True)

    def check_deadline():
        require(time.monotonic() - started < args.max_seconds,
                f"Rehearsal exceeded the {args.max_seconds}s target")

    def get(base, path, token=None):
        status, _, data = request(base, path, token=token)
        require(status == 200, f"GET {path}: HTTP {status}")
        return data

    def post(base, path, body=None, token=None):
        check_deadline()
        status, _, data = request(base, path, "POST", body, token=token, timeout=30)
        require(status in (200, 201), f"POST {path}: HTTP {status}")
        return data

    health = get(args.base, "/api/health")
    expected_sha = args.expected_sha or health["git_sha"]
    result = smoke(args.base, expected_sha, operator_user, operator_password, tokens)
    operator = tokens.token(args.base, operator_user, operator_password)
    audit = get(args.base, "/api/audit?limit=1", operator)
    require(isinstance(audit, list), "Audit endpoint did not return a list")
    status, _, _ = request(args.base, "/api/internal/alerts")
    require(status == 405, "Alert webhook route is absent; rebuild the backend first")
    instance = get(args.simulator, "/v1/instance")
    evidence["build"] = {"git_sha": health["git_sha"], "version": health.get("version")}
    evidence["simulator"] = {key: instance.get(key) for key in
                             ("scenario_id", "scenario_version", "seed", "tick_minutes", "status")}
    record("preflight", result)
    if not args.allow_reset:
        evidence["status"] = "preflight_passed"
        evidence["limits"] = "No simulator state changed. Add --allow-reset to execute the rehearsal."
        return

    for target in (args.base, args.simulator):
        require(urlparse(target).hostname in ("localhost", "127.0.0.1", "::1"),
                "Mutation is restricted to loopback hosts; use a local/disposable stack")
    admin = tokens.token(args.base, admin_user, admin_password)
    reset_done = False
    fault_active = False
    try:
        post(args.base, "/api/control/sim/reset", {"confirm": True}, admin)
        reset_done = True
        post(args.base, "/api/control/sim/pause", token=admin)
        post(args.base, "/api/settings/autopilot", {"mode": "manual"}, admin)
        baseline = get(args.base, "/api/network/state", operator)
        require(baseline["tick"] == 0, "Reset was not reflected by the backend")
        station_ids = {station["id"] for station in baseline["stations"]
                       if station["region_id"] == args.region}
        require(station_ids, f"No stations in {args.region}")
        baseline_risk = {station["id"]: {fuel: info["risk"] for fuel, info in station["fuels"].items()}
                         for station in baseline["stations"] if station["id"] in station_ids}
        record("reset_and_manual_mode", {"tick": 0, "run_id": baseline["run_id"],
                                         "baseline_risk": baseline_risk})

        event = {"type": "demand_spike", "start_tick": 1, "duration_ticks": 96,
                 "parameters": {"region_ids": [args.region], "multiplier": args.multiplier}}
        post(args.base, "/api/control/events", event, admin)
        record("demand_spike_scheduled", {"event": event})
        proposal = None
        for _ in range(64):
            post(args.base, "/api/control/sim/step", token=admin)
            network = get(args.base, "/api/network/state", operator)
            recs = get(args.base, "/api/recommendations", operator)
            eligible = [rec for rec in recs if rec["station_id"] in station_ids
                        and rec["impact"]["risk_before"] >= 0.2]
            if eligible:
                proposal = max(eligible, key=lambda rec: rec["impact"]["risk_before"])
                break
        require(proposal is not None, "No shortage proposal for the affected region within 64 ticks")
        alerts = get(args.base, "/api/alerts", operator)
        require(any(alert.get("status") == "OPEN" for alert in alerts), "No open shortage alert")
        forecast = get(args.base,
                       f"/api/stations/{proposal['station_id']}/forecast?fuel={proposal['fuel_type']}", operator)
        record("shortage_prediction_and_recommendation", {
            "tick": network["tick"], "station": proposal["station_id"], "fuel": proposal["fuel_type"],
            "risk_before": proposal["impact"]["risk_before"], "risk_after": proposal["impact"]["risk_after"],
            "hours_to_stockout": forecast.get("hours_to_stockout"), "action": proposal["action"],
            "open_alerts": sum(alert.get("status") == "OPEN" for alert in alerts)})

        approved = post(args.base, f"/api/recommendations/{proposal['id']}/approve", {}, operator)
        key = approved["request"]["idempotency_key"]
        record("operator_approval", {"execution_id": approved["id"], "actor": approved.get("actor"),
                                      "idempotency_key": key, "method": "authenticated operator API"})
        observed = []
        for _ in range(64):
            allocations = get(args.simulator, "/v1/allocations")
            matching = [row for row in allocations if row.get("idempotency_key") == key]
            require(len(matching) == 1, "Expected exactly one matching simulator allocation")
            allocation = matching[0]
            if not observed or observed[-1] != allocation["status"]:
                observed.append(allocation["status"])
            require(allocation["status"] not in ("FAILED", "CANCELLED"), "Shipment did not arrive")
            if allocation["status"] == "ARRIVED":
                break
            post(args.base, "/api/control/sim/step", token=admin)
        require(allocation["status"] == "ARRIVED", "Shipment did not arrive within 64 ticks")
        decisions = get(args.base, "/api/decisions", operator)
        require(any(row["request"]["idempotency_key"] == key for row in decisions),
                "Shipment missing from decision history")
        record("shipment_arrived", {"status_sequence": observed, "actual_arrival_tick": allocation.get("actual_arrival_tick"),
                                     "decision_history": True})

        fault_started = time.monotonic()
        fault_active = True
        post(args.simulator, "/admin/faults", {"type": "unavailable", "duration_seconds": 90, "parameters": {}})

        def degraded():
            current = get(args.base, "/api/network/state", operator)
            require(current.get("mode") == "DEGRADED", "Degradation not yet visible")
            require(current.get("execution_blocked") is True, "Execution gate stayed open")
            require(current.get("stations"), "Cached station state is missing")
            ready_status, _, _ = request(args.base, "/api/health/ready")
            require(ready_status == 503, "Readiness should be 503 during outage")
            get(args.base, "/api/health/live")
            return {"mode": current["mode"], "execution_blocked": True, "cached_stations": len(current["stations"])}

        degraded_result = eventually(degraded, timeout=45)
        record("failure_detected", {**degraded_result, "detection_seconds": round(time.monotonic() - fault_started, 3)})
        # Clear through the simulator admin endpoint, which remains available during faults.
        post(args.simulator, "/admin/faults/clear")
        fault_active = False
        recovery_started = time.monotonic()
        eventually(lambda: smoke(args.base, expected_sha, operator_user, operator_password, tokens), timeout=90)
        record("recovered", {"mode": "NORMAL", "recovery_seconds": round(time.monotonic() - recovery_started, 3)})
        check_deadline()
        evidence["status"] = "passed"
        evidence["limits"] = ("Timed API rehearsal, not a human-presented or video-recorded rehearsal. "
                              "Monitoring alert delivery is verified separately. Simulator ends paused in manual mode.")
    finally:
        # Never leave our injected fault active after a failed assertion. These calls do not
        # retry an uncertain mutation and their result is included even when the run failed.
        cleanup = []
        for needed, base, path, token in (
            (fault_active, args.simulator, "/admin/faults/clear", None),
            (reset_done, args.base, "/api/control/sim/pause", admin),
        ):
            if needed:
                try:
                    status, _, _ = request(base, path, "POST", token=token, timeout=30)
                    cleanup.append({"action": path, "status": status})
                    if status != 200:
                        evidence["status"] = "failed"
                except OSError as error:
                    cleanup.append({"action": path, "error": str(error)})
                    evidence["status"] = "failed"
        evidence["cleanup"] = cleanup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:3000")
    parser.add_argument("--simulator", default="http://127.0.0.1:8000")
    parser.add_argument("--expected-sha", help="Require the running build to report this SHA")
    parser.add_argument("--allow-reset", action="store_true", help="Reset this local/disposable simulator and run the full rehearsal")
    parser.add_argument("--region", default="region-dhaka")
    parser.add_argument("--multiplier", type=float, default=2.5)
    parser.add_argument("--max-seconds", type=int, default=480)
    parser.add_argument("--output", default="artifacts/timed-rehearsal.json")
    args = parser.parse_args()
    started = time.monotonic()
    evidence = {"status": "started", "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "command": ["python", "scripts/timed_rehearsal.py", *sys.argv[1:]],
                "machine": {"platform": platform.platform(), "processor": platform.processor()},
                "target_seconds": args.max_seconds, "stages": []}
    try:
        run(args, evidence)
    except (AssertionError, OSError, ValueError, KeyError, TypeError) as error:
        evidence["status"] = "failed"
        evidence["error"] = str(error)
    finally:
        evidence["duration_seconds"] = round(time.monotonic() - started, 3)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0 if evidence["status"] in ("passed", "preflight_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
