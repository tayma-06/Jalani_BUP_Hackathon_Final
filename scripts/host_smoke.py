"""Use host Compose configuration in memory; never print resolved secrets."""
import json
import subprocess
import sys
from pathlib import Path

from app_smoke import smoke
from http_checks import eventually
from manifest import validate
from verify_images import verify

root, release, output = map(Path, sys.argv[1:4])
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
result = eventually(lambda: smoke("http://127.0.0.1:3000", manifest["git_sha"],
                                  environment["OPERATOR_USER"], environment["OPERATOR_PASSWORD"]), timeout=90)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
