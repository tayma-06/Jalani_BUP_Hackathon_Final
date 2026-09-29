# Evidence checklist: real results only

A configuration file is not operational evidence. Every DONE row below points to a measured artifact. See [validation-status.md](validation-status.md) for details.

| Evidence | Status | File/link |
|---|---|---|
| Local automation validation | DONE | `docs/validation-status.md` |
| End-to-end working app and source repository | DONE locally | `docs/evidence/app-*.png`; repository `github.com/tayma-06/BUP_Hackathon_Final` (push pending) |
| Official simulator integration | DONE | `docs/evidence/simulator-contract.json`, image `sha256:7067050693f4…` |
| Demand/risk/valid allocation with explanation | DONE | `docs/evidence/e2e-drill.json` (proposal, risk before/after), `docs/evidence/app-recommendations.png` |
| Human approval + durable idempotency/restart proof | DONE | Backend tests `test_concurrent_approvals_create_one_durable_intent`, `test_lost_accepted_response_reconciles_after_restart`; drill `duplicate_approval` (2 × HTTP 200 → 1 simulator allocation) |
| Red PR followed by repaired green PR | NOT RUN | Needs GitHub Actions |
| Passing tagged release | NOT RUN | Needs GitHub Actions + GHCR |
| Deployed version and SHA | Local only | `docs/evidence/app-smoke.json` (running SHA checked). No remote host |
| Failed candidate and verified rollback | NOT RUN on a host | Script logic covered by `tests_ci/test_deployment.py` (fake Docker) |
| Simulator fault and recovery | DONE | `docs/evidence/fault-recovery.json`, `docs/evidence/app-system-health.png` |
| DB failure blocks execution | Test only | `test_db_failure_blocks_post_but_preserves_cache` |
| Monitoring: application/system/intelligence/logs | DONE (no alert delivery) | `docs/evidence/grafana-*.png`; structured JSON event logs via `docker compose logs backend` |
| Meaningful application load test | DONE | `docs/load-test.md`, `docs/evidence/load-dashboard.json` |
| Fair policy comparison | DONE | `docs/benchmark.md`, `docs/evidence/benchmark-baseline.json`, `docs/evidence/benchmark-stress.json` |
| Architecture and setup | DONE | `README.md`, `docs/architecture.md`, `docs/troubleshooting.md` |
| Final rehearsal | NOT RUN | `docs/demo-script.md` is ready; record the timing and a backup video |

For every new run, record: date, git SHA, simulator image, configured speed and tick length, scenario/events, machine CPU/RAM, exact command, result, and limitation. Keep passwords, keys, the full `.env` and the resolved Compose configuration out of evidence.
