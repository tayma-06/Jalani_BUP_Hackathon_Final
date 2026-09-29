"""Verify baked image revisions; an environment variable alone is not proof of code identity."""
import json
import subprocess
import sys
from pathlib import Path

from manifest import validate


def check_revision(image_metadata, expected_sha):
    labels = image_metadata.get("Config", {}).get("Labels") or {}
    if labels.get("org.opencontainers.image.revision") != expected_sha:
        raise ValueError("Baked image revision does not match the release manifest")


def verify(data):
    data = validate(data)
    result = {}
    for service in ("backend", "frontend"):
        reference = data[service + "_image"]
        metadata = json.loads(subprocess.check_output(["docker", "image", "inspect", reference], text=True))[0]
        check_revision(metadata, data["git_sha"])
        result[service] = {"reference": reference, "image_id": metadata["Id"]}
    return result


if __name__ == "__main__":
    result = verify(json.loads(Path(sys.argv[1]).read_text()))
    print(json.dumps({"image_revisions": "verified", "images": result}))
