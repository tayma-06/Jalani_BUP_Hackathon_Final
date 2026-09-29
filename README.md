# Jalani: fuel supply control room

Jalani sits on top of the BUP hackathon fuel-supply simulator. It watches every depot, station and route, forecasts demand, estimates stockout risk, and recommends valid fuel shipments with reasons. An operator approves each shipment. When the simulator or database fails, Jalani keeps showing the last good data and refuses to act on bad data.

Everything shown is a **simulated environment**. No real infrastructure is controlled.

## Quick start (Docker, recommended)

Requirements: Docker Desktop (or Docker Engine + Compose v2). No other tools are needed.

```bash
docker compose up -d --build --wait     # simulator, postgres, backend, frontend, prometheus, grafana
```

| Open | URL | Login |
|---|---|---|
| **App** | http://localhost:3000 | `operator` / `demo-operator` |
| App, admin (adds Control room) | same | `admin` / `demo-admin` |
| App, read-only | same | `viewer` / `demo-viewer` |
| Backend API | http://localhost:8080/api/health | — |
| Simulator | http://localhost:8000/v1/health | — |
| Prometheus | http://localhost:9090 | — |
| Grafana dashboard | http://localhost:3001 | anonymous view; admin `admin` / `demo-grafana` |

The simulator starts **paused**. Sign in as admin → **Control room** → **Run**.

Every setting has a demo default. To change passwords, the simulator speed and so on, run `cp .env.example .env` and edit it. **Change every password and `JWT_SECRET` before exposing the app beyond your machine.** Never commit `.env`.

Stop with `docker compose down`. That keeps the decision history; `down -v` deletes it.

> **Build fails with `certificate verify failed`?** Antivirus or proxy HTTPS scanning is intercepting TLS. See [docs/troubleshooting.md](docs/troubleshooting.md#starting-the-stack) and set `EXTRA_CA_FILE` in `.env`.

## Running without Docker (development)

Python 3.11+ and Node 22+ are required. The simulator still runs in Docker:

```bash
docker run -d --name jalani-simulator -p 127.0.0.1:8000:8000 -e SIMULATOR_START_MODE=paused \
  asifmahmoud414/bup-fuel-supply-simulator:1.0.0

# backend (uses SQLite ./jalani.db by default)
cd backend
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.lock
uvicorn app.main:app --port 8080

# frontend, in another terminal (proxies /api to :8080)
cd frontend && npm ci && npm run dev                   # http://localhost:3000
```

## Tests

```bash
pip install -r backend/requirements-dev.lock
python -m ruff check backend
PYTHONPATH=backend python -m pytest backend/tests        # 24 behavioural tests
python -m unittest discover -s tests_ci                  # 14 CI/CD automation tests
cd frontend && npm ci && npm run lint && npm run typecheck && npm run test:ci && npm run build
```

On Linux, macOS or WSL, `make test` runs all of the above; `make help` lists every target.

The backend tests cover the nine mandatory behaviours in [docs/ci-contract.md](docs/ci-contract.md):
- batch constraints
- concurrent approvals producing one shipment
- a lost response reconciled after a restart
- changed-body rejection
- stale data blocking writes
- DB failure blocking writes
- paused-simulator propagation
- reset scoping
- human review and role checks

## Verification against the real simulator

These scripts drive the running stack. The ones marked **destructive** reset the simulator, so use them only on a local or disposable stack, never during a demo.

| Command | What it proves | Output |
|---|---|---|
| `python scripts/app_smoke.py --base http://127.0.0.1:3000 --expected-sha dev` | Frontend, auth, health, fresh state, recommendations | `artifacts/app-smoke.json` |
| `python scripts/simulator_contract.py --simulator http://127.0.0.1:8000 --allow-reset` **(destructive)** | Official API contract, idempotent POST, cancel | `artifacts/simulator-contract.json` |
| `python scripts/fault_probe.py --simulator http://127.0.0.1:8000 --base http://127.0.0.1:3000 --expected-sha dev --isolated-ci` **(destructive)** | Stale/unavailable detection, cached reads, readiness 503, recovery | `artifacts/fault-recovery.json` |
| `python scripts/e2e_drill.py --base http://127.0.0.1:3000 --simulator http://127.0.0.1:8000 --allow-reset` **(destructive)** | Roles, over-limit rejection, double-approval → one shipment, delivery, disruption, demand spike | `artifacts/e2e-drill.json` |
| `python scripts/benchmark.py --base <isolated backend> --simulator <isolated sim> --scenario stress --output artifacts/benchmark-stress.json --allow-reset` **(destructive)** | Do-nothing vs naive vs greedy on the same seed and events (`baseline` or `stress`) | `artifacts/benchmark-*.json`, [docs/benchmark.md](docs/benchmark.md) |
| k6 load test, see [docs/load-test.md](docs/load-test.md) | Dashboard read latency under concurrent users | `artifacts/load-dashboard.json` |

`app_smoke.py`, `fault_probe.py` and `e2e_drill.py` read `SMOKE_USER` / `SMOKE_PASSWORD` (and `ADMIN_PASSWORD` etc.) from the environment. Set `SMOKE_USER=operator SMOKE_PASSWORD=demo-operator` for the local defaults.

## Project layout

```
backend/app/            FastAPI app: main.py (API), service.py (refresh, gates, execution),
                        intelligence/engine.py (forecast, risk, allocation), sim/ (client, schemas), db.py
backend/config/         demand profiles and allocation policy (YAML)
backend/tests/          behavioural tests + a fake simulator used ONLY by tests
frontend/src/           React operator console (App.tsx) and its tests (App.test.tsx)
docker/                 backend/frontend Dockerfiles, nginx config
docker-compose.yml      local/demo stack        compose.ci.yml   disposable CI stack
deploy/                 host compose + env template for the release pipeline
observability/          Prometheus scrape + alert rules, Grafana provisioning + dashboard
scripts/                smoke, contract, fault, drill, benchmark, release, deploy, rollback
loadtest/dashboard.js   k6 scenario
.github/workflows/ci.yml  lint → test → build images → official-simulator checks → release → deploy
```

## Documentation

| Doc | For |
|---|---|
| [docs/architecture.md](docs/architecture.md) | How it works: pipeline, safety gates, failure behavior |
| [docs/demo-script.md](docs/demo-script.md) | The 8-minute judge demo |
| [docs/troubleshooting.md](docs/troubleshooting.md) | When something doesn't work |
| [docs/benchmark.md](docs/benchmark.md) | Policy comparison results |
| [docs/load-test.md](docs/load-test.md) | Load test results |
| [CI_CD_GUIDE.md](CI_CD_GUIDE.md) | CI/CD, release, deployment and rollback |
| [plan.md](plan.md) | Full product plan and constraints |
| [docs/validation-status.md](docs/validation-status.md) | What has been verified, and what has not |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SIMULATOR_URL` | `http://simulator:8000` | Point at any simulator instance, including the judges' own |
| `SIMULATION_SPEED` | `1` | 1 ≈ 96 s per simulated day; the simulator's own default is 8 |
| `DATABASE_URL` | Postgres in Compose, `sqlite:///./jalani.db` locally | Durable decisions and history |
| `JWT_SECRET` | demo value | ≥ 32 characters; change it for any shared deployment |
| `*_USER` / `*_PASSWORD` | `viewer`, `operator`, `admin` | The three roles |
| `CHAOS_ENABLED` | `false` | Demo-only crash / corrupt-response buttons |
| `ML_SERVICE_URL`, `LLM_API_KEY` | empty | Optional; empty uses the local statistical forecast and template explanations |
| `EXTRA_CA_FILE` | none | Only behind TLS-intercepting antivirus or proxies |

## Known limitations

- Single host and a single backend worker: no high availability.
- Forecast risk and confidence are model estimates, not calibrated probabilities.
- Supply in the scenario is finite. Long enough runs end in shortage whatever the policy does, and the app switches to rationing mode.
- GitHub Actions, image release and SSH deployment are configured but have not been run; see [docs/validation-status.md](docs/validation-status.md).
