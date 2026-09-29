# Validation status: 29 September 2026

Verified on an Intel i7-14650HX / 15.7 GB / Windows 11 machine with Docker Desktop, against the official simulator image `asifmahmoud414/bup-fuel-supply-simulator:1.0.0` (`sha256:7067050693f4…`).

## Passed

| Check | Result | Evidence |
|---|---|---|
| Backend lint (ruff) | Clean, on Python 3.11 in a container | CI command, run locally |
| Backend behavioral tests | **33 / 33** on Python 3.11 (Linux container) and 3.14 (Windows) | `pytest backend/tests` |
| CI/CD automation tests | **16 / 16** on Python 3.11 (Linux). On Windows, 3 symlink tests need Developer Mode | `unittest discover -s tests_ci` |
| Frontend lint, typecheck, tests, build | Clean; **17 / 17** tests on Node 22 (CI version) and Node 24. Entry bundle 316 kB, chart code split into a separate on-demand chunk under a 400 kB build budget | `npm run lint / typecheck / test:ci / build` |
| Pinned dependency locks | `backend/requirements.lock`, `backend/requirements-dev.lock` (Python 3.11), `frontend/package-lock.json` | Installed cleanly in fresh containers |
| Docker images build; full stack starts healthy | simulator, postgres, backend, frontend, prometheus, grafana | `docker compose up -d --build --wait` |
| Official simulator contract | Passed: idempotent replay returns the same allocation, changed body rejected, pending cancel works | `docs/evidence/simulator-contract.json` |
| App smoke through nginx | Passed: HTML served, live/ready 200, NORMAL mode, fresh data (0.4 s), 4 stations | `docs/evidence/app-smoke.json` |
| Fault injection and recovery | `stale_data` and `unavailable` detected in ~1.1 s, cached reads stayed available, readiness 503, recovered in ~1.1 s | `docs/evidence/fault-recovery.json` |
| End-to-end drill | Role 403s, over-limit 422, double approval → exactly 1 shipment, delivery IN_TRANSIT → ARRIVED, disrupted route avoided, demand spike raised 3 alerts | `docs/evidence/e2e-drill.json` |
| Browser check | All 9 pages, desktop 1440 px and mobile 390 px: no horizontal overflow, no console errors, charts render. The audit-log page was added after this pass; its behaviour is covered by frontend tests | `docs/evidence/app-*.png` |
| Policy benchmark | 3 policies × 2 scenarios × 288 ticks, same seed and events | [benchmark.md](benchmark.md), `docs/evidence/benchmark-*.json` |
| Load test | 30 users, 0 failures, p95 132 ms, all thresholds passed | [load-test.md](load-test.md), `docs/evidence/load-dashboard.json` |
| Monitoring | Prometheus scrapes the backend (UP), Alertmanager receives and groups alerts, and the backend records them at `/api/internal/alerts`. Every dashboard query returns live data. Grafana provisions the 20-panel dashboard | `docs/evidence/grafana-*.png` |
| Compose files | `docker-compose.yml`, `compose.ci.yml` and `deploy/compose.yml` pass `docker compose config` | — |
| Durable growth is bounded | Retention caps hold under repeated refreshes, a simulator reset and a restart; no table grows without limit | `backend/tests/test_retention.py` |

## Not run

| Check | Why |
|---|---|
| GitHub Actions run, red→green PR, GHCR image release | The workflow is ready, but it has not been triggered on GitHub from this machine. |
| SSH deployment, rollback on a real host, data continuity | No deployment host or credentials. `tests_ci` exercises the deploy/rollback script logic against a fake Docker only. |
| Prometheus alert firing end to end | The rules load and Alertmanager is wired and started by both stacks, but no alert was observed firing against the live simulator, so the delivery path is verified by configuration and by the backend's ingest tests rather than by a real page. |
| DB-outage drill on the live stack | Covered by a backend test (`test_db_failure_blocks_post_but_preserves_cache`), not demonstrated live. |
| Forecast calibration / accuracy study | Not measured. Risk values are model estimates. |
| Final timed rehearsal | Needs the event's actual time limit. |

The earlier version of this file described the planning pack before any application existed. The raw output of that earlier validation is kept in `docs/local-validation.txt`.
