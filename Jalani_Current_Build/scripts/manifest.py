"""Validate a release and convert only allowlisted fields into Compose variables."""
import argparse
import json
import re
from pathlib import Path

IMAGE = re.compile(r"ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
VERSION = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")


def validate(data):
    for key in ("backend_image", "frontend_image"):
        if not isinstance(data.get(key), str) or not IMAGE.fullmatch(data[key]):
            raise ValueError(f"{key} must be an immutable GHCR sha256 reference")
    if not isinstance(data.get("git_sha"), str) or not SHA.fullmatch(data["git_sha"]):
        raise ValueError("git_sha must be a complete 40-character commit SHA")
    if not isinstance(data.get("version"), str) or not VERSION.fullmatch(data["version"]):
        raise ValueError("version must have the form v1.2.3")
    return data


def env_text(data):
    data = validate(data)
    return "".join(f"{env}={data[key]}\n" for env, key in (
        ("BACKEND_IMAGE", "backend_image"), ("FRONTEND_IMAGE", "frontend_image"),
        ("APP_VERSION", "version"), ("GIT_SHA", "git_sha")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--env-output", required=True)
    args = parser.parse_args()
    data = json.loads(Path(args.manifest).read_text())
    Path(args.env_output).write_text(env_text(data))
    print(data["git_sha"])
