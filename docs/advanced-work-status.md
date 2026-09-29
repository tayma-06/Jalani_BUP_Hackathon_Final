# Advanced work status

## Merge into `main` (29 September 2026)

`feature/advanced-completion` (6934829) was merged into `main` on top of the later commits: the alerting pipeline, retention limits, audit log and README. Three files conflicted, and each was resolved by keeping both sides:

- **`db.py`:** main's startup indexes plus this branch's migration ledger.
- **`service.py`:** main's history trimming and pruning plus this branch's rejection cooldown.
- **`tests_ci/test_deployment.py`:** skip unless the host is POSIX **and** symlinks are available.

One behavior clash only appeared once the two sides were combined:
- **The clash:** main's retention pruning deleted every non-open recommendation without an execution, so this run's `REJECTED` rows vanished and the cooldown could never suppress a repeat. `test_rejected_proposal_is_not_immediately_repeated` failed.
- **The fix:** pruning now keeps the current run's rejections, capped at the newest `KEEP_RECOMMENDATIONS`.
- **The guard:** `test_rejections_survive_pruning_for_the_cooldown_but_are_capped` covers it.

Results after the merge:
- Backend: ruff clean; **73 passed** on Python 3.11 (Linux container) and 3.14 (Windows).
- `tests_ci`: **22 passed** on Linux. On Windows, the 5 deployment tests are skipped.

The newly created `model_versions`, `drift_state`, `forecast_evaluations`, `agent_runs`, `app_events` and `leases` tables are not written by any code yet. Drift state is held in memory. Trained ML forecasting, the LLM assistant and the other items listed below remain unimplemented.

---

Branch: `feature/advanced-intelligence` (from `main` @ `1c0a765`)
Environment: Windows 11, Python 3.12.10, Node 24.15.0, Docker Desktop 29.8.0.
Official simulator used for integration work: `asifmahmoud414/bup-fuel-supply-simulator:1.0.0`
(container `jalani-sim`, scenario `baseline`, seed `12345`). **Never modified.**

## 1. Baseline verification (before any behaviour change)

| Command | Result |
| --- | --- |
| `python -m ruff check backend` | passed |
| `PYTHONPATH=backend python -m pytest backend/tests -q` | **24 passed** |
| `python -m unittest discover -s tests_ci` | **11 passed, 3 errors** (pre-existing, see below) |
| `cd frontend && npm run lint` | passed |
| `cd frontend && npm run typecheck` | passed |
| `cd frontend && npm run test:ci` | **15 passed** |
| `cd frontend && npm run build` | passed |

### Pre-existing failure diagnosed (not a regression)

`tests_ci/test_deployment.py` raised `OSError: [WinError 1314] A required privilege is not
held by the client` from `setUp`, for all three `DeploymentTests`. Cause: the release layout
under test is a POSIX symlink chain (`current -> releases/<sha>`), and the fixture `docker`
command is a `#!/usr/bin/env python3` script. Windows does not grant symlink creation to an
unelevated process, so the fixture cannot be built at all.

Fix applied: a capability probe (`symlinks_available()`) that skips the class with a recorded
reason instead of skipping on the platform name. No assertion was removed or weakened, and on
POSIX the three tests still execute the real `deploy.sh` control flow. Post-fix result:
`11 passed, 3 skipped`.

This failure existed at the baseline commit; it is not caused by any change in this branch.

## 2. Audit summary: what is real, and what is only configured

### Implemented and backed by automated evidence at baseline

- Typed simulator client with retry/backoff, circuit breaker, stale-header detection, strict
  pydantic validation and a two-sided snapshot consistency check.
- Durable execution: immutable `jal-<run>-<rec>` idempotency key and body committed **before**
  the simulator POST; 4xx → `FAILED`, timeout → `UNKNOWN`; ledger reconciliation on every
  refresh; `ABANDONED_RESET` for intents from a superseded run.
- Execution gates in order: DB check → `safe()` → fresh `/v1/instance` → `validate_batch` →
  durable intent → POST.
- Monte-Carlo stockout risk with arrivals applied before demand and common random numbers
  across counterfactuals.
- Batch validator shared by propose, approve and retry.
- Alert detection, acknowledgement, resolution and reopen-by-occurrence.
- Reset detection and run-scoped identifiers.
- JWT roles, audit log, Prometheus metrics, Compose stack, CI/CD workflow, fault probe,
  contract script, e2e drill, policy benchmark, k6 dashboard load test.

### Documented or configured but **not** implemented (gap list before this branch)

| Area | Finding |
| --- | --- |
| Alert event scoping | `engine.detect` attached **every** active simulator event to **every** alert, so an unrelated crisis appeared on unrelated stations. |
| Counterfactual independence | `recommend` computed "do nothing" / "half shipment" / alternative-route risk with `extra_arrivals` (the *other* not-yet-approved shipments from the same proposal batch) already counted as delivered. Displayed benefit silently assumed unapproved shipments. |
| Duplicate proposals | `persist_intelligence` computed its "already proposed" set once, while `recommend` may emit several shipments for the same station+fuel, so a second proposal for the same pair could be persisted. |
| Rejected proposals | Nothing stopped an identical proposal being regenerated on the very next cycle after a rejection. |
| Policy registry | `POST /api/settings/policy` overwrote a single row. No version history, no actor/reason, no previous version, no rollback, no revalidation of outstanding recommendations. |
| Inventory reconciliation | No "unexplained inventory change" detection where reconciliation is reliable. |
| Trained ML forecasting | `ML_SERVICE_URL` exists in config and Compose. **No implementation.** `health` reports a hard-coded `"model": "profile_v1"`. |
| Constrained optimisation | No LP/MILP policy. Only `greedy_v1` and `naive_reorder`. |
| Drift detection | None. |
| Operations assistant | `LLM_API_KEY` exists in config. `health` hard-codes `"status": "disabled"`. `/api/summary` is a fixed template. |
| Multi-agent coordination | Single inline pipeline in `service._refresh` / `engine.recommend`. |
| RL | None. |
| Application event stream | The backend *consumes* simulator SSE but publishes no operator event stream. |
| Kubernetes | No manifests, no HPA, no DB-backed leadership. Compose only. |
| Migrations | `Base.metadata.create_all` only. No versioned migration path for schema changes. |
| Multi-replica safety | `asyncio.Lock` is in-process only. Explicitly called out as a limitation in `docs/architecture.md`. |

### Documented assumptions that remained unverified at baseline

- `docs/validation-status.md` marks Docker build, simulator contract/faults, GitHub Actions
  execution, SSH deployment, Prometheus/Grafana runtime, k6 measurements and forecast
  calibration as **not run**.
- `CURRENT_BUILD_STATUS.txt` states backend tests were "not completed" and the official
  simulator integration was "not run (Docker unavailable)". Both are now stale: Docker is
  available and the backend suite passes. This file is superseded by the present document.

## 3. Phase log

### Phase 0 — audit, branch, baseline

- Branch `feature/advanced-intelligence` created from `main` @ `1c0a765`; working tree clean.
- Fixed the pre-existing `tests_ci` Windows symlink error (capability probe, assertions intact).
- Baseline recorded above.

Status: **Implemented and verified.**
