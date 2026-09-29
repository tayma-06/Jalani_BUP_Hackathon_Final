"""Fail clearly until real application source, tests and dependency locks are present."""
import json
from pathlib import Path

required = ["backend/app/main.py", "backend/requirements.lock", "backend/requirements-dev.lock",
            "frontend/package.json", "frontend/package-lock.json", "frontend/src",
            "docker/backend.Dockerfile", "docker/frontend.Dockerfile"]
missing = [name for name in required if not Path(name).exists()]
if not list(Path("backend/tests").glob("test_*.py")):
    missing.append("backend/tests/test_*.py (real behavioral tests)")
if Path("frontend/package.json").exists():
    scripts = json.loads(Path("frontend/package.json").read_text()).get("scripts", {})
    missing += ["frontend script: " + name for name in ("lint", "typecheck", "test:ci", "build")
                if not scripts.get(name)]
if missing:
    raise SystemExit("Application prerequisites missing; this build pack is not the application:\n- "
                     + "\n- ".join(missing))
print("Application CI prerequisites found")
