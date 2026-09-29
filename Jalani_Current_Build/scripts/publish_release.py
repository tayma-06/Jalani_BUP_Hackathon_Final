"""Publish already-built and tested local images, then record immutable identities."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from manifest import validate
from verify_images import check_revision

repository = os.environ["GITHUB_REPOSITORY"].lower()
sha, version = os.environ["GITHUB_SHA"], os.environ["GITHUB_REF_NAME"]
if not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", repository):
    raise SystemExit("Unexpected repository name")
if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version):
    raise SystemExit("Release tags must be vX.Y.Z")
data = {"version": version, "git_sha": sha}
for service in ("backend", "frontend"):
    local = json.loads(subprocess.check_output(["docker", "image", "inspect", f"jalani-{service}:ci"], text=True))[0]
    check_revision(local, sha)
    repo = f"ghcr.io/{repository}-{service}"
    tag = f"{repo}:sha-{sha}"
    subprocess.run(["docker", "tag", f"jalani-{service}:ci", tag], check=True)
    subprocess.run(["docker", "push", tag], check=True)
    inspected = json.loads(subprocess.check_output(["docker", "image", "inspect", tag], text=True))[0]
    choices = [d for d in inspected.get("RepoDigests", []) if d.startswith(repo + "@sha256:")]
    if len(choices) != 1:
        raise SystemExit(f"Could not resolve unique pushed digest for {service}")
    data[service + "_image"] = choices[0]
    readable_tag = f"{repo}:{version}"
    subprocess.run(["docker", "tag", tag, readable_tag], check=True)
    subprocess.run(["docker", "push", readable_tag], check=True)
validate(data)
bundle = Path("release-bundle")
(bundle / "scripts").mkdir(parents=True, exist_ok=True)
(bundle / "manifest.json").write_text(json.dumps(data, indent=2) + "\n")
shutil.copy2("deploy/compose.yml", bundle / "compose.yml")
for name in ("deploy.sh", "rollback.sh", "host_smoke.py", "app_smoke.py", "http_checks.py", "manifest.py", "verify_images.py"):
    shutil.copy2(Path("scripts") / name, bundle / "scripts" / name)
Path("artifacts").mkdir(exist_ok=True)
subprocess.run(["tar", "-czf", "artifacts/release-bundle.tgz", "-C", str(bundle), "."], check=True)
shutil.copy2(bundle / "manifest.json", "artifacts/manifest.json")
print(json.dumps(data, indent=2))
