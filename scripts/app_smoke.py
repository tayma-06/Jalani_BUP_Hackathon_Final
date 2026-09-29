"""Read-only application smoke test, including authenticated frontend proxy reads."""
import argparse
import json
import os
from pathlib import Path

from http_checks import eventually, login, request, require


def smoke(base, expected_sha, username, password):
    status, _, html = request(base, "/")
    require(status == 200 and isinstance(html, str) and "<html" in html.lower(),
            "Frontend did not serve HTML")
    for path in ("/api/health/live", "/api/health/ready"):
        status, _, _ = request(base, path)
        require(status == 200, f"{path}: HTTP {status}")
    status, _, health = request(base, "/api/health")
    require(status == 200 and isinstance(health, dict), "Overall health invalid")
    require(health.get("git_sha") == expected_sha, "Running git SHA does not match release")
    require(health.get("mode") == "NORMAL", "Application has not recovered to NORMAL")
    token = login(base, username, password)
    status, _, state = request(base, "/api/network/state", token=token)
    require(status == 200 and isinstance(state, dict), "Network read failed")
    require(state.get("stale") is False, "State is stale or freshness metadata is missing")
    age = state.get("data_age_s")
    require(isinstance(age, (int, float)) and 0 <= age <= 15, "Snapshot older than 15 seconds")
    require(state.get("mode") == "NORMAL", "Network state is degraded")
    require(isinstance(state.get("stations"), list) and len(state["stations"]) > 0,
            "No discovered stations")
    status, _, recs = request(base, "/api/recommendations", token=token)
    require(status == 200 and isinstance(recs, list), "Recommendations must return a JSON array")
    return {"status": "passed", "git_sha": health["git_sha"], "mode": health["mode"],
            "station_count": len(state["stations"]), "tick": state.get("tick"),
            "recommendation_count": len(recs), "data_age_s": age}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:13000")
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", default="artifacts/app-smoke.json")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    username, password = os.environ["SMOKE_USER"], os.environ["SMOKE_PASSWORD"]
    result = eventually(lambda: smoke(args.base, args.expected_sha, username, password), timeout=args.timeout)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
