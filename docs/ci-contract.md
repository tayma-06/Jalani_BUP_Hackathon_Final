# Contract between the application team and CI/CD

The starter intentionally expects these files and behaviors. Adapt it to a real repository deliberately; do not make a missing feature return a fake success just to turn CI green.

## Repository contract

| Path | Expected content |
|---|---|
| `backend/app/main.py` | FastAPI `app` object, served by Uvicorn on 8080 |
| `backend/requirements.lock` | Pinned runtime dependencies, including uvicorn, FastAPI, DB driver, and application dependencies |
| `backend/requirements-dev.lock` | Pinned runtime + ruff + pytest and required test plugins; install runtime requirements too |
| `backend/tests/test_*.py` | Meaningful application behavior tests, not empty placeholders |
| `frontend/package.json` | Scripts: `lint`, `typecheck`, `test:ci`, `build`; `test:ci` must exit, e.g. vitest run |
| `frontend/package-lock.json` | Committed npm lockfile for `npm ci` |
| `frontend/src/` | Real React operator app; Vite output is `dist/` |

The original plan used `requirements.txt`; keep it as human-edited input if desired, but generate and commit the locked files consumed by CI. Use Python 3.11 and Node 22 consistently locally and in CI. The Python CI command sets `PYTHONPATH=backend`. The selected SQLAlchemy URL uses psycopg, so include an appropriate pinned `psycopg` installation in the runtime lock.

## HTTP contract

| Endpoint | Expected behavior |
|---|---|
| `POST /api/auth/login` | JSON `{username,password}` → HTTP 200 `{access_token,role}`; invalid credentials fail |
| `GET /api/health/live` | Public; 200 when process is responsive, including upstream outages |
| `GET /api/health/ready` | Public; 200 only with initialized/reconciled state, usable DB and sufficiently fresh non-stale snapshot; otherwise 503 |
| `GET /api/health` | Public summary includes `git_sha`, `version`, `mode`, components; no credentials |
| `GET /api/network/state` | Bearer auth; top-level `stations` array, `mode`, `stale`, `data_age_s`, `tick`, plus plan.md contract; cached reads remain 200 in degraded mode |
| `GET /api/recommendations` | Bearer auth; JSON array. Empty is valid when there is no useful recommendation |
| `GET /metrics` | Prometheus exposition, internal access; process and real simulator-read metrics |
| `POST /api/decide?dry_run=true` | Authenticated operator; no simulator writes or persisted decision side effects |

`APP_VERSION` and `GIT_SHA` must be read by the backend and displayed in health. The frontend uses Vite build variables and/or the health endpoint. A 200 response with an old SHA must fail deployment.

No ML or LLM service is required for this starter. Empty `ML_SERVICE_URL` and `LLM_API_KEY` must activate documented local forecasting/template fallbacks. Disabled optional services are not critical readiness failures.

## Mandatory behavioral tests to implement with application code

1. Constraints across a batch: inventory, route size/status, station headroom including pending/in-transit/reserved quantities, dispatch budget, and ownership of route endpoints.
2. Two simultaneous approvals produce one persisted shipment intent; retries reuse its immutable key/body.
3. Accepted POST + lost response + backend restart reconciles one allocation, without issuing a new key.
4. Changed body cannot reuse an occupied key; a definitive rejected plan and an unknown outcome are handled differently.
5. Stale, invalid, over-age or inconsistent snapshots block both manual and automatic writes.
6. DB failure blocks execution before any simulator POST; cached reads remain available and labeled.
7. Allocation/fault changes while paused still propagate. SSE reconnect triggers full REST refresh.
8. Reset/run scoping prevents reused simulator row IDs colliding with older history. Ambiguous external resets cause reconciliation.
9. Human review remains required for consequential/crisis actions; UI role restrictions are enforced by the backend too.

The included fault probe covers stale/unavailable visibility, cached reads, liveness/readiness separation, and recovery. It does **not** prove all nine cases above, nor ML/SSE/DB failure behavior. Retain separate tests and drills for them.

## Environment and deployment assumptions

- One Linux amd64 host, Docker Engine with Compose v2 supporting `up --wait`, Python 3.11+, Bash, `flock`, GNU coreutils, SSH.
- Deployment root `/srv/jalani` belongs to the deploy user; this user may run Docker. It is a powerful credential: keep it only in the trusted deployment environment.
- Host DB credentials in `DATABASE_URL` match the Postgres service. Real `.env` is mode 600 and not committed or uploaded as evidence.
- First boot starts `simulator` and `db` separately. Application updates use `--no-deps`, preserving the running simulator and DB volume.
- The guide does not document the simulator's internal persistence path. This pack deliberately does not invent a volume mount. Recreating that container or losing its host storage may reset the world; rehearse and document this limitation.
- Compose is single-host, with a short replacement interruption. No claim of high availability or zero downtime.
- Base image tags are starter defaults; after a real build/pull record or pin actual digests. Releases of the application itself deploy by digest.
