"""Verify a real Prometheus rule -> Alertmanager -> application -> resolved lifecycle.

ONLY for a disposable/local demo: injects one bounded stale-data fault and clears it in
finally. It does not reset or advance the simulator. The five-minute Alertmanager group
interval can make resolution take several minutes. No webhook token is needed: this
exercises the configured receiver, not a direct post to the application webhook.
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from http_checks import eventually, login, request, require

ALERT_NAME = "StaleSimulatorData"


def run(base, simulator, prometheus, alertmanager, user, password, evidence, timeout=420):
    started = time.monotonic()
    token = login(base, user, password)
    evidence.update({"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                     "alert": ALERT_NAME, "checks": {}})

    def ready():
        status, _, _ = request(base, "/api/health/ready")
        require(status == 200, "Application must be healthy before injecting a fault")
        status, _, data = request(prometheus, "/api/v1/alertmanagers")
        active = data.get("data", {}).get("activeAlertmanagers", []) if isinstance(data, dict) else []
        require(status == 200 and active, "Prometheus has no active Alertmanager")
        status, _, _ = request(alertmanager, "/-/ready")
        require(status == 200, "Alertmanager is not ready")
        return active

    evidence["checks"]["active_alertmanagers"] = eventually(ready, timeout=90)
    status, _, audit_rows = request(base, "/api/audit?limit=1", token=token)
    require(status == 200 and isinstance(audit_rows, list), "Audit endpoint unavailable")
    previous_audit_id = audit_rows[0]["id"] if audit_rows else 0

    def rule_firing():
        status, _, data = request(prometheus, "/api/v1/alerts")
        require(status == 200, "Prometheus alert query failed")
        rows = [a for a in data["data"]["alerts"] if a["labels"].get("alertname") == ALERT_NAME
                and a["state"] == "firing"]
        require(rows, "Stale-data rule has not reached firing")
        return rows

    def delivered(expected):
        status, _, rows = request(base, "/api/alerts", token=token)
        require(status == 200, "Application alerts unreadable")
        matches = [a for a in rows if a.get("source") == "prometheus"
                   and a.get("alertname") == ALERT_NAME and a["status"] == expected]
        require(matches, f"Application has no {expected} monitoring alert")
        row = matches[-1]
        require(row.get("message") and isinstance(row.get("event_ids"), list),
                "Monitoring alert is missing fields required by the Alerts page")
        status, _, rows = request(base, "/api/audit?limit=1000", token=token)
        action = "alert.firing" if expected == "OPEN" else "alert.resolved"
        entries = [a for a in rows if a["id"] > previous_audit_id and a["target"] == row["id"]
                   and a["actor"] == "alertmanager" and a["action"] == action]
        require(status == 200 and entries, f"No new audit record for {action}")
        return {"alert_id": row["id"], "status": row["status"], "message": row["message"],
                "audit_id": entries[0]["id"], "elapsed_seconds": round(time.monotonic() - started, 2)}

    # The bounded duration also recovers the simulator if this process is interrupted.
    try:
        status, _, _ = request(simulator, "/admin/faults", "POST",
                               {"type": "stale_data", "duration_seconds": 600, "parameters": {}})
        require(status in (200, 201), "Stale-data fault injection failed")
        print("Injected stale data; waiting for the real Prometheus rule.", flush=True)
        evidence["checks"]["prometheus_firing"] = eventually(rule_firing, timeout=60, interval=2)
        evidence["checks"]["application_firing"] = eventually(lambda: delivered("OPEN"),
                                                                timeout=timeout, interval=2)
        print("Alertmanager delivered OPEN to the application with an audit record.", flush=True)
    finally:
        status, _, _ = request(simulator, "/admin/faults/clear", "POST")
        require(status == 200, "Could not clear the probe's simulator fault")

    def recovered():
        status, _, _ = request(base, "/api/health/ready")
        require(status == 200, "Backend readiness has not recovered")
        status, _, data = request(prometheus, "/api/v1/alerts")
        require(status == 200, "Prometheus alert query failed")
        require(not any(a["labels"].get("alertname") == ALERT_NAME for a in data["data"]["alerts"]),
                "Prometheus still reports the stale-data rule")
        return {"ready": True, "rule_inactive": True}

    evidence["checks"]["recovery"] = eventually(recovered, timeout=90, interval=2)
    print("Backend recovered; waiting for Alertmanager's grouped RESOLVED notification.", flush=True)
    evidence["checks"]["application_resolved"] = eventually(lambda: delivered("RESOLVED"),
                                                              timeout=timeout, interval=2)
    evidence.update({"status": "passed", "duration_seconds": round(time.monotonic() - started, 2),
                     "scope": "Real Prometheus rule evaluation, Alertmanager delivery, application alert and audit APIs. "
                              "Browser rendering is verified separately."})
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:3000")
    parser.add_argument("--simulator", default="http://127.0.0.1:8000")
    parser.add_argument("--prometheus", default="http://127.0.0.1:9090")
    parser.add_argument("--alertmanager", default="http://127.0.0.1:9093")
    parser.add_argument("--allow-fault", action="store_true", required=True)
    parser.add_argument("--output", default="artifacts/alert-delivery.json")
    args = parser.parse_args()
    evidence = {}
    try:
        run(args.base, args.simulator, args.prometheus, args.alertmanager,
            os.environ.get("SMOKE_USER", "operator"), os.environ.get("SMOKE_PASSWORD", "demo-operator"), evidence)
    except Exception as error:
        evidence.update({"status": "failed", "error": str(error)})
        raise
    finally:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, indent=2))
