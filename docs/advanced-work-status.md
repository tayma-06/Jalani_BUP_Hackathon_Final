# Advanced work status

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

This table records the state of `main` before this branch, not the current state. Of these gaps,
alert scoping, counterfactual independence, duplicate/rejected proposals, the policy registry,
inventory reconciliation, ML forecasting, constrained optimisation, drift detection and the
operations assistant have since been implemented — the last two of those still lack live
environment evidence, and the table's "not implemented" rows for them should not be read as
current. Multi-agent coordination, RL, the application event stream and Kubernetes remain open.

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

### Phase 1 — corrections to advanced behaviour already present (part 1)

- `engine.scoped_event_ids`: an alert attaches only the ACTIVE events that can actually reach
  its entity, resolved through topology. A route alert matches only that route; a station
  alert also matches its region; a depot alert matches the depot and every route it owns.
- `engine.reevaluate_review`: re-runs the review gate against the **current** snapshot and
  reports `(reasons, newly)` where `newly` means a reason was added since the proposal.
- `service.approve` re-checks the review gate immediately before execution and **blocks**
  AUTOPILOT with a reason when an active crisis or route disruption now reaches the shipment.
- `recommend` counterfactuals are independent: "Do nothing"/"Half shipment"/alternative-route
  risk never credits unapproved sibling shipments. Impact basis states `This shipment only.`,
  and `plan` reports coordinated figures (`shipments_for_entity`, `sibling_proposals`,
  `risk_after_all_approved`, `unmet_after_all_approved_l`) as a separate, disclosed number.
- `engine.inventory_reconciliation`: flags unexplained inventory deltas only where
  reconciliation is reliable (complete history for the window, no station OUTAGE).
- Duplicate proposals: `persist_intelligence` maintains its proposed-set while looping, so a
  station+fuel is proposed at most once per run.
- Rejection cooldown: `rejection_suppressed()` with `rejection_cooldown_ticks=8` and
  `rejection_risk_margin=0.15`, persisted on the rejected recommendation row
  (`suppression`, `suppressed_until_tick`); a materially worse risk re-proposes immediately.
- Versioned policy registry (`backend/app/policy_registry.py`): `register`, `rollback`,
  `active_version`, `history`, `supersede_stale_recommendations`, audited activation/rollback,
  no-op re-registration refused, name reuse with a different configuration refused, rollback
  without a predecessor refused.
- `db.py`: records `SchemaMigration`, `PolicyVersion`, `ModelVersion`, `DriftState`,
  `AgentRun`, `AppEvent`, `Lease`, `ForecastEvaluation`; `MIGRATIONS` 0001-0006; additive
  `Database.initialize()` with policy-registry backfill.
- `main.py`: `GET /api/policies`, `POST /api/settings/policy` (registry write), `POST
  /api/settings/policy/rollback`, `active` configuration endpoint honouring the registry.
- `tests_ci` symlink probe retained; ruff clean.

Phase 1 evidence: `backend/tests/test_advanced.py` (24 tests) — all pass; full backend suite
**48 passed**; `tests_ci` `11 passed, 3 skipped`; `ruff check backend` passed.

Status: **Implemented and verified.**

### Phase 2 — ML forecasting, model registry, drift detection, operations assistant

Merged from `feature/advanced-completion` (merge `8806134`) plus the trained forecast work.

- `backend/app/intelligence/ml.py`: trained per-(station, fuel) ridge regression over
  calendar + lagged-history features. Chronological split — the last `eval_rows` ticks are
  held out and never appear in training; lagged features are built only from strictly earlier
  observations. Artifacts carry `checksum` (sha256 of canonical JSON), `feature_schema`,
  `feature_schema_version`, `data_fingerprint` and `training_config`.
- `engine.forecast(..., forecaster=)` uses a trained model when it covers the station+fuel and
  otherwise falls back to `profile_v1`; `analyse(..., forecaster=)` threads it through.
  `normalized_history` is now shared by both paths so the baseline series is identical.
- `service.active_forecaster()` loads the active artifact from `model_versions` and **validates
  checksum + schema before use**; a corrupt or incompatible artifact yields `None` and the
  service silently keeps `profile_v1`. Health reports the real model name, never a hard-coded one.
- API: `GET /api/models/forecast`, `POST /api/models/forecast/deploy` (admin, checksum enforced),
  `GET /api/models/forecast/evaluations`.
- `backend/app/intelligence/drift.py` + `test_drift.py`: windowed input-drift monitor.
- `backend/app/assistant.py` + `test_assistant.py`: read-only grounded assistant with template
  fallback; `POST /api/assistant`; config `GROQ_API_KEY` / `GROQ_MODEL` / `LLM_TIMEOUT_SECONDS`
  (empty key = disabled, template explanations). No execution rights.
- Frontend: `Assistant.tsx` + `Assistant.test.tsx` (16 vitest tests).

Phase 2 evidence: `pytest backend/tests -q` **60 passed**; `tests_ci` `11 passed, 3 skipped`;
`ruff check backend` clean; frontend `lint`, `typecheck`, `test:ci` (16 passed) and `build` pass.

Status: **Implemented; the training run is now complete — see Phase 3 below for the measured
error and the two defects it exposed.** Drift detection is still verified on fixtures only.

### Phase 3 — constrained optimisation (`lp_v1`, `lp_ml_v1`) and the training pipeline

**Optimiser.** `backend/app/intelligence/optimize.py`. The decision is how many litres to ship
on each feasible route, so the model is a linear program whose constraint matrix is a network
matrix. The LP relaxation therefore has integral vertices, and the exact optimum is a min-cost
flow, solved here with successive shortest paths and Johnson potentials — pure NumPy, no
`scipy`/`pulp` dependency was added, and no mixed-integer solver is claimed.

```
source -> depot:fuel -> gate_in -> gate_out -> station:fuel -> { tank sink | unmet sink }
                                (one shared arc = the depot dispatch budget, across all fuels)
```

Objective, per litre, with the weights in `backend/config/policy.yaml` under `optimiser`:
unmet demand (`unmet_penalty` + `priority_weight` x fuel weight), transport effort
(`transport_weight` x transit ticks), late delivery (`late_penalty` when arrival is after the
projected stockout), and unnecessary movement (`surplus_weight` above the station's net need).
Depot stock, shared depot dispatch, route shipment limits, station tank space, delivery timing,
the home-region reserve and entity status are all enforced by the network itself.

Safety: every plan is re-checked with the shared `validate_batch` before it is returned. If the
solve fails, times out, or the plan fails validation, the cycle falls back to `greedy_v1` and
each proposal carries a `solver` block naming `fallback_from` and the reason — a degraded
decision is visible, never silent. `benchmark.py` counts those fallbacks per run
(`fallback_decisions`, 0 in the run below).

**Training pipeline.** `scripts/train_forecast_model.py` (target `make train-model`) samples the
simulator's rolling demand-history window while stepping a disposable instance, builds a
leak-safe chronological dataset per station+fuel, trains, verifies the artifact checksum, and
writes `artifacts/forecast-model.json` plus `artifacts/forecast-training-report.json`. It refuses
to report a metric it cannot compute, and the official simulator binary is untouched.

Two real defects were found and fixed by running these paths for real:

1. `model_checksum` hashed the artifact including its own `checksum` field, so a stored digest
   could never be re-verified — a trained artifact raised `ModelError` the moment it was loaded
   back, which would have made deployment impossible. The field is now excluded from the hash.
   Regression test: `test_artifact_checksum_survives_a_round_trip`, plus tamper and
   schema-drift refusals in `backend/tests/test_ml.py`.
2. Rounding each shipment to whole litres independently pushed a depot one litre past its
   shared dispatch budget. The optimiser now rounds down and re-checks route, tank, dispatch and
   stock limits as it books each shipment, so the plan it returns is one the shared validator
   accepts. The shared-validator check remains as the final backstop.

#### Real training run (disposable official simulator, seed 12345, scenario `baseline`)

2400 ticks stepped on an isolated container; 24 000 unique demand-history rows collected; 12
station+fuel models trained (1 247 training rows each, 96 held out), 0 skipped.

| Metric | Value |
| --- | --- |
| Model version | `ml-ridge-v1` |
| Checksum | `99cf6afa56ade9a3…` |
| Data fingerprint | `a6154798d8f3dfa1…` |
| Stations trained / evaluated | 12 / 12 |
| Per-station MAE | 2.28 L (`tongi:OCTANE`) … 14.97 L (`tongi:DIESEL`) |
| Per-station WMAPE | 0.0994 (`tongi:PETROL`) … 0.1125 (`tongi:DIESEL`) |
| Mean WMAPE across stations | **0.1005** |

The held-out window is the 96 most recent ticks, and each row's features use only strictly
earlier observations, so these are forward-looking errors rather than in-sample fits.

#### Real benchmark (disposable official simulator, 192 ticks = 2 sim days, seed 12345)

`scripts/benchmark.py` scores every policy from the simulator's own `/v1/metrics`, after
approving every proposal, so these are simulator outcomes and not model estimates.

| Policy | Service level | Unmet (L) | Approved litres | Shipments | Approvals blocked | Solver fallbacks | Allocation failures |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `do_nothing` | 0.4604 | 100 452.1 | 0 | 0 | 0 | 0 | 0 |
| `naive_reorder` | 1.0000 | 0.0 | 193 220 | 47 | 4 | 0 | 0 |
| `greedy_v1` | 1.0000 | 0.0 | 181 278 | 45 | 0 | 0 | 0 |
| `lp_v1` | 1.0000 | 0.0 | **148 506** | 164 | 0 | 0 | 0 |

**What this does and does not show.** On this scenario the LP policy reaches the same 100%
service level as `greedy_v1` while moving **32 772 L less fuel (18.1% fewer litres)**, because
it ships each station's net need instead of filling tanks to a 24-hour target. It reaches that
outcome in 164 smaller shipments rather than 45 larger ones, and is slower per decision cycle
(419 s vs 243 s over 192 ticks) because it solves a network problem every cycle. It is not
faster and it is not universally better: on a scenario where tanks are already near full, or
where routing is the binding constraint rather than volume, the litre saving will shrink. This
is one seeded scenario on one scenario definition, not a general performance claim.

#### Live end-to-end verification on the running stack

Beyond unit tests, both features were exercised through the real API against the isolated
Compose stack (the official `jalani-sim` on 18000 was never reset; all destructive work ran on a
disposable project and container).

- **Artifact round-trip.** `POST /api/models/forecast/deploy` accepted the trained artifact and
  returned checksum `99cf6afa…`, identical to the value in the training report — the step the
  self-referential-checksum bug would have made impossible. Re-deploying the same artifact is
  idempotent (`changed: false`) and does not create a spurious version.
- **The model actually drives forecasts.** `GET /api/stations/{id}/forecast` reports
  `source: "trained ridge forecast ml-ridge-v1"` and per-station series that are clearly
  modelled rather than the flat profile — e.g. `station-tongi:OCTANE` rises from 10.6 L/h
  overnight to 39.1 L/h midday, `station-karnaphuli:OCTANE` from 47.0 to 52.4 L/h, with
  station-specific residual spread (3.71 / 10.23 / 4.53 L).
- **The LP policy is live and auditable.** Activating `lp_v1` through
  `POST /api/settings/policy` records actor, reason, `previous_version` and a run id in the
  versioned registry. Proposals then carry a `solver` block naming the policy that produced
  them, per-entity unmet demand, litres moved, late shipments, litres dropped below the minimum
  shipment, the objective breakdown and the weights used. Under real scarcity the solver
  reported `unmet_liters: 15172.0` and every proposal came back `requires_review: true`, so a
  plan the solver could not fully cover reaches a human rather than executing silently.

A third defect surfaced only in this live pass, and would have been invisible to the unit tests
because they assert on `MLForecast.version` rather than on what the API returns: the forecast
endpoint reported `model_version: "ml-ml-ridge-v1"` from a redundant `f"ml-{version}"` prefix,
so the version shown to operators did not match the version stored in the registry and used for
rollback. Fixed, with a regression test that was confirmed to fail against the old expression.

#### Environment note for anyone re-running this

`.env` pins `SIMULATOR_URL=http://127.0.0.1:18000` for the host-run backend. A **containerised**
backend must not inherit that value, because inside the container `127.0.0.1` is the backend
itself, not the simulator: the client fails, the circuit breaker opens within seconds, and every
call returns `CIRCUIT_OPEN` / 503. The benchmark run initially failed this way. The fix is to
override it for the containerised stack:

```
$env:SIMULATOR_URL="http://simulator:8000"   # PowerShell, before docker compose up
docker compose -p jalani-bench up -d --build --wait simulator db backend
```

This is a configuration trap rather than a code defect, but it produces a misleading error
(`CIRCUIT_OPEN: Simulator circuit is cooling down`) that looks like a breaker bug.

## 4. Advanced feature status (as of the Phase 3 commit)

Branch `feature/advanced-intelligence`. All results below come from this workspace; nothing is
inherited from a run that was not executed here.

| # | Feature | Status | Files | Verification command | Result |
| --- | --- | --- | --- | --- | --- |
| 1 | Uncertainty-aware allocation | Implemented and verified | `engine.project` | `pytest backend/tests -q` | 118 passed |
| 2 | Counterfactual independence | Implemented and verified | `engine.recommend` | `pytest -k shown_benefit` | passed |
| 3 | Incident detection + event scoping | Implemented and verified | `engine.detect`, `scoped_event_ids` | `pytest -k scoped` | passed |
| 4 | Versioned policy registry + rollback | Implemented and verified | `policy_registry.py` | `pytest -k registry or rollback` | passed |
| 5 | Event-driven updates (SSE) | Implemented and verified (unchanged by this branch) | `service.stream` | `pytest backend/tests -q` | 118 passed |
| 6 | Trained ML forecast + model registry | **Implemented, trained and deployed** | `intelligence/ml.py`, `scripts/train_forecast_model.py` | `make train-model`; `pytest backend/tests/test_ml.py` | 21 tests pass; 12/12 station+fuel models trained on a disposable official simulator, mean held-out WMAPE **0.1005**; deployed through `POST /api/models/forecast/deploy` with checksum `99cf6afa…` round-tripping, and live forecasts report `source: trained ridge forecast ml-ridge-v1` |
| 7 | Constrained optimization policy | **Implemented, benchmarked and live** | `intelligence/optimize.py`, `config/policy.yaml` | `pytest backend/tests/test_optimize.py`; `scripts/benchmark.py` | 39 solver/safety/fallback tests pass; `lp_v1` reaches service level 1.0000 with 148 506 L approved vs `greedy_v1` 181 278 L, 0 solver fallbacks, 0 allocation failures; activated live via the policy registry and every proposal carries an auditable `solver` block |
| 8 | Drift detection | Implemented; environment verification pending | `intelligence/drift.py` | `pytest backend/tests/test_drift.py` | passes on fixtures only; no live drift run |
| 9 | Grounded operations assistant | Implemented; not approved for operation | `assistant.py` | `pytest backend/tests/test_assistant.py` | passes; disabled unless `GROQ_API_KEY` set |
| 10 | Multi-agent coordination | Not implemented | - | - | Single inline pipeline remains |
| 11 | Experimental RL policy | Not implemented | - | - | No adapter, no checkpoints |
| 12 | Application event stream | Not implemented (SSE consume only) | - | - | Backend publishes no operator stream |
| 13 | Kubernetes + safe autoscaling | Not implemented | - | - | Compose only; no cluster available |

**What is now evidence-backed, and what is not.** Features 6 and 7 have real measured numbers
from a disposable official simulator, recorded above: the forecast model's held-out error, and
the optimiser's service level and litres moved against the other three policies. Those figures
come from one seeded scenario on one scenario definition; they are not a general performance
claim, and the optimiser's litre advantage is specific to a world where tank fill, not routing,
was the binding constraint.

Three real defects surfaced only because these paths were executed rather than inspected, and
all three are now fixed with regression tests: an artifact checksum that could never be
re-verified, a whole-litre rounding step that could breach the shared dispatch budget, and a
`model_version` reported to operators that did not match the version stored in the model
registry. Reading the code alone had not exposed any of the three, and the third was missed by
the unit tests until the live API was called.

Features 8-13 remain unverified or unimplemented. The branch is pushed nowhere; commits
`bceeb5f`, `8806134` (merge), `d39a197` and `c5ecae8` are local.

## 5. Still open

- **Feature 8 (drift)**: no live drift episode has been induced and observed. A unit test proves
  the detector fires on a shifted window; nothing has shown it firing on the running stack.
- **Features 10-13** (multi-agent coordination, RL adapter and checkpoints, operator event
  stream, Kubernetes manifests and HPA) are not started.
- **Feature 9** works on fixtures but has never been run against a live `GROQ_API_KEY`, so its
  grounding behaviour under real responses is unverified.
- The optimiser's benchmark is one scenario and one seed. Before any claim about the litre
  saving generalises, it needs the other scenario definitions and several seeds, which is
  destructive work and belongs on a disposable stack like the one used here.
- `main` has not been merged; the branch is local and unpushed.
