"""Use host Compose configuration in memory; never print resolved secrets."""
import json
import subprocess
import sys
from pathlib import Path

from app_smoke import smoke
from http_checks import TokenCache, eventually, request, require
from manifest import validate
from verify_images import verify


def monitoring_smoke():
    status, _, _ = request("http://127.0.0.1:9093", "/-/ready")
    require(status == 200, "Alertmanager is not ready")
    status, _, managers = request("http://127.0.0.1:9090", "/api/v1/alertmanagers")
    require(status == 200 and managers.get("status") == "success", "Prometheus discovery failed")
    active = managers.get("data", {}).get("activeAlertmanagers", [])
    require(any("alertmanager:9093" in manager.get("url", "") for manager in active),
            "Prometheus has not discovered Alertmanager")
    status, _, targets = request("http://127.0.0.1:9090", "/api/v1/targets")
    require(status == 200 and targets.get("status") == "success", "Prometheus targets unavailable")
    backend = [target for target in targets.get("data", {}).get("activeTargets", [])
               if target.get("labels", {}).get("job") == "jalani-backend"]
    require(len(backend) == 1 and backend[0].get("health") == "up", "Backend metrics scrape is unhealthy")
    return {"status": "passed", "alertmanager_count": len(active), "backend_scrape": "up"}


def run(root, release, output):
    manifest = validate(json.loads((release / "manifest.json").read_text()))
    command = ["docker", "compose", "--project-name", "jalani", "--env-file", str(root / ".env"),
               "--env-file", str(release / "release.env"), "-f", str(release / "compose.yml"),
               "config", "--format", "json"]
    images = verify(manifest)
    for service, image in images.items():
        ids = subprocess.check_output(command[:-3] + ["ps", "-q", service], text=True).split()
        if len(ids) != 1:
            raise SystemExit(f"Expected one running {service} container")
        container = json.loads(subprocess.check_output(["docker", "container", "inspect", ids[0]], text=True))[0]
        if container["Image"] != image["image_id"]:
            raise SystemExit(f"Running {service} image differs from the manifest")
    resolved = json.loads(subprocess.check_output(command, text=True))
    environment = resolved["services"]["backend"]["environment"]
    tokens = TokenCache()
    result = eventually(lambda: smoke("http://127.0.0.1:3000", manifest["git_sha"],
                                      environment["OPERATOR_USER"], environment["OPERATOR_PASSWORD"],
                                      tokens), timeout=90)
    if "alertmanager" in resolved["services"]:
        result["monitoring"] = eventually(monitoring_smoke, timeout=90)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(run(*map(Path, sys.argv[1:4]))))
