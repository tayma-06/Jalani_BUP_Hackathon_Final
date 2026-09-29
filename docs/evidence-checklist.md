# Evidence checklist: real results only

The chosen final target is **local Docker**. Configuration is not operational evidence, and an earlier build's evidence does not validate a newer deployment. See [validation-status.md](validation-status.md) for measured results and limitations.

| Evidence | Status | File/link |
|---|---|---|
| Latest source tests | Backend 33 passed; frontend 17 passed; rerun pending monitoring fixes | `docs/validation-status.md` |
| Complete CI automation tests | Windows: 11 passed, 5 symlink errors; Linux run pending | `tests_ci/` |
| Current local Docker deployment and build identity | Verification in progress | New smoke evidence required; historic `docs/evidence/app-smoke.json` reports `local-verify` |
| Complete source committed and submitted | Pending final commit/push | `github.com/tayma-06/BUP_Hackathon_Final` |
| Official simulator integration | Earlier measured pass | `docs/evidence/simulator-contract.json` |
| Demand/risk/allocation explanation | Earlier measured pass; current rehearsal pending | `docs/evidence/e2e-drill.json`, `docs/evidence/app-recommendations.png` |
| Human-role approval and durable idempotency | Tests and earlier drill | Backend tests; `duplicate_approval` in `docs/evidence/e2e-drill.json` |
| Passing GitHub Actions run | Pending | Record run URL and commit SHA after push |
| Passing tagged image release | Pending | Record release/tag and image digest if run |
| Local failed-candidate/rollback and data continuity | Pending runtime proof | Script logic alone is covered by `tests_ci/test_deployment.py` |
| Remote deployment and rollback | Optional, out of selected scope | Local Docker is the final deployment |
| Simulator fault and recovery | Earlier measured pass; current rehearsal pending | `docs/evidence/fault-recovery.json` |
| Database failure blocks execution | Test only | `test_db_failure_blocks_post_but_preserves_cache` |
| Monitoring dashboards | Earlier measured screenshots | `docs/evidence/grafana-*.png` |
| Prometheus -> Alertmanager -> app firing and resolved | Current runtime verification pending | Requires measured delivery and resolution, not just loaded rules |
| Meaningful application load test | Earlier measured pass | `docs/load-test.md`, `docs/evidence/load-dashboard.json` |
| Fair policy comparison | Earlier measured pass | `docs/benchmark.md`, `docs/evidence/benchmark-*.json` |
| Architecture and setup | Present | `README.md`, `docs/architecture.md`, `docs/troubleshooting.md` |
| Timed machine rehearsal | Script prepared; run pending | `scripts/timed_rehearsal.py` |
| Timed human presentation and backup video | Not recorded | `docs/demo-script.md`; eight-minute schedule prepared |

For each new run, record the UTC date, build/commit identity, simulator image and scenario, seed, speed or explicit stepping, tick length, machine, exact command, elapsed time, result and limitations. Keep passwords, bearer tokens, private keys, full `.env` files and expanded Compose configuration out of evidence.
