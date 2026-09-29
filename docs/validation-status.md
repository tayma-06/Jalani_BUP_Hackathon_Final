# Validation status: 29 September 2026

The final deployment target is **local Docker**. A remote SSH host is optional and is outside the selected deliverable. Source checks, archived runtime evidence and verification of the current build are recorded separately below.

## Current source checks

| Check | Latest observed result | Evidence / command |
|---|---|---|
| Backend lint | Passed | `python -m ruff check backend` |
| Backend behavioral tests | **33 passed** before the monitoring integration fixes; rerun pending | `PYTHONPATH=backend python -m pytest backend/tests` |
| Frontend tests | **17 passed** before the monitoring integration fixes; rerun pending | `npm run test:ci` |
| Frontend type checking | Passed | `npm run typecheck` |
| CI/CD automation on Windows | **11 passed; 5 symlink-creation errors** | `python -m unittest discover -s tests_ci`; run on Linux to verify the complete suite |
| Timed rehearsal script | Ruff and CLI help passed; runtime pending | `scripts/timed_rehearsal.py` |

Test counts describe the measured run, not a permanent claim about the size of the suite. The last current-source test run is distinct from GitHub Actions completion.

## Current local deployment verification

| Check | Status |
|---|---|
| Rebuild and restart from complete current source | In progress; final build identity and health still need recording |
| `/api/audit` and `/api/internal/alerts` in running backend | Pending after rebuild. The previous running image lacked both routes |
| Prometheus connected to Alertmanager | Pending after restart. The previous runtime reported no active Alertmanager |
| Alert fires, appears in the application and resolves | Pending a real end-to-end monitoring drill; configuration and unit tests alone do not prove delivery |
| Timed demand spike -> shortage -> recommendation -> operator approval -> arrival -> failure -> recovery | Prepared in `scripts/timed_rehearsal.py`; measured run pending |
| Local update and rollback with durable data | Pending runtime verification |
| Commit/push and GitHub Actions | Pending a final commit and GitHub run; local tests do not establish a GitHub result |

## Archived measured evidence

These artifacts are retained from earlier runs against the official simulator. They support their recorded scenarios and builds; they are not proof that the latest image has been deployed.

| Check | Recorded result | Evidence |
|---|---|---|
| Official simulator contract | Idempotent replay returned the same allocation; changed body rejected; pending cancel worked | `docs/evidence/simulator-contract.json` |
| App smoke through nginx | HTML served; liveness/readiness 200; NORMAL mode; 4 stations. Recorded build identity is `local-verify` | `docs/evidence/app-smoke.json` |
| Fault injection and recovery | Stale/unavailable faults detected in about 1.1 s; cached reads remained available; readiness 503; recovered in about 1.1 s | `docs/evidence/fault-recovery.json` |
| End-to-end drill | Roles enforced, oversized approval rejected, duplicate approval created one shipment, shipment arrived, disrupted route avoided, demand spike raised 3 alerts; 4.6 s API drill | `docs/evidence/e2e-drill.json` |
| Browser review | Screenshots cover desktop/mobile views of the earlier build. They do not establish a browser pass for the later audit and monitoring changes | `docs/evidence/app-*.png` |
| Policy benchmark | Three policies, two scenarios, 288 ticks; both active policies served all demand in these runs | [benchmark.md](benchmark.md), `docs/evidence/benchmark-*.json` |
| Load test | 30 concurrent users; zero failed requests; p95 131.6 ms | [load-test.md](load-test.md), `docs/evidence/load-dashboard.json` |
| Grafana monitoring | Earlier screenshots show provisioned dashboards; they do not establish alert delivery | `docs/evidence/grafana-*.png` |

The archived simulator is `asifmahmoud414/bup-fuel-supply-simulator:1.0.0`; the benchmark documents scenario, seed, tick length and machine. Historic raw planning checks remain in `docs/local-validation.txt`.

## Remaining limits

- A timed API rehearsal does not measure speaking time or create a backup recording. Use [demo-script.md](demo-script.md) for the eight-minute presentation and record a human run separately.
- Forecast error and risk calibration have not been measured. Risk values are model estimates.
- A live database-outage drill has not been recorded; backend tests cover the write gate and cached-read behavior.
- Remote SSH deployment and rollback are optional, outside the selected local Docker delivery. Script tests use fake Docker and are not evidence of a remote deployment.
