"""Inject faults only in disposable CI. Proves visibility and recovery, not all write gates."""
import argparse
import json
import os
import time
from pathlib import Path

from app_smoke import smoke
from http_checks import eventually, login, request, require


def run(simulator, base, sha, user, password):
    eventually(lambda: smoke(base, sha, user, password))
    token = login(base, user, password)
    results = []
    for kind in ("stale_data", "unavailable"):
        start = time.monotonic()
        try:
            status, _, _ = request(simulator, "/admin/faults", "POST",
                                   {"type": kind, "duration_seconds": 90, "parameters": {}})
            require(status in (200, 201), "Fault injection failed")
            health_status, _, _ = request(simulator, "/v1/health")
            require(health_status == 200, "Simulator liveness should bypass injected faults")

            def degraded():
                status, _, data = request(base, "/api/health")
                require(status in (200, 503) and data.get("mode") == "DEGRADED",
                        "Degradation not yet visible")
                status, _, _ = request(base, "/api/health/live")
                require(status == 200, "Process should stay live during an upstream fault")
                status, _, state = request(base, "/api/network/state", token=token)
                require(status == 200 and len(state.get("stations", [])) > 0,
                        "Last-known state should remain readable")
                require(state.get("mode") == "DEGRADED", "Cached state lacks degraded marker")
                if kind == "stale_data":
                    require(state.get("stale") is True, "Stale header not propagated")
                status, _, _ = request(base, "/api/health/ready")
                require(status == 503, "Readiness must fail during critical degradation")
                return state

            eventually(degraded, timeout=45)
            detected = time.monotonic() - start
        finally:
            status, _, _ = request(simulator, "/admin/faults/clear", "POST")
            require(status == 200, "Could not clear isolated test faults")
        recovery_start = time.monotonic()
        eventually(lambda: smoke(base, sha, user, password), timeout=90)
        results.append({"fault": kind, "status": "passed", "detection_seconds": round(detected, 3),
                        "recovery_seconds": round(time.monotonic() - recovery_start, 3)})
    return {"status": "passed", "checks": results,
            "limits": "Write blocking, DB failure, lost-response reconciliation and SSE recovery require backend tests and separate drills."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator", default="http://127.0.0.1:18000")
    parser.add_argument("--base", default="http://127.0.0.1:13000")
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--isolated-ci", action="store_true", required=True)
    parser.add_argument("--output", default="artifacts/fault-recovery.json")
    args = parser.parse_args()
    result = run(args.simulator, args.base, args.expected_sha,
                 os.environ["SMOKE_USER"], os.environ["SMOKE_PASSWORD"])
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
