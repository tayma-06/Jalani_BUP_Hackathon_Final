# Independent advanced-work verification

> **Superseded.** These failures were fixed before `feature/advanced-completion` was pushed. That branch alone passed 50/50 backend tests. After it was merged into `main`, the combined suite passed **73/73** on Python 3.11 (Linux) and 3.14 (Windows). See the merge note in [advanced-work-status.md](advanced-work-status.md).

Checked 29 September 2026 against the working checkout. This is a test observation,
not a claim that the advanced work is complete. Another process was changing
`backend/app/intelligence/engine.py` during the review; rerun after edits settle.

Command: `.venv\Scripts\python.exe -m pytest backend/tests -q`

Result: **39 passed, 9 failed** (12.81 seconds).

Failures in `backend/tests/test_advanced.py`:

| Test area | Observed failure | Next verification |
| --- | --- | --- |
| Route/depot event scoping | An unrelated route receives event 7 | Verify event type and target IDs for each entity type |
| Demand spike expiration | Expected anomaly absent | Reconcile fixture tick 24 with event end tick 5 and ACTIVE status |
| Inventory reconciliation (three tests) | Fixture accesses `Snapshot.inventory` | Access station inventory and preserve complete tick history before testing reconciliation |
| Rejection cooldown | A proposed recommendation remains after rejection | Distinguish existing fixture proposals from regenerated proposals; verify actual rejection flow |
| Rollback without predecessor | Expected message differs from actual no-predecessor error | Test no predecessor and already-active target separately |
| Policy invalidation | Test orders by nonexistent `Recommendation.fuel_type` | Read fuel from payload or order using real columns |
| Concurrent approval | Validation after execution counts shipment again | Validate against pre-execution snapshot; separately assert one intent and one simulator POST |

Remaining advanced areas listed by the existing audit: trained ML forecasting,
LP/MILP optimization, drift detection, operations assistant, operator event stream,
migrations, multi-replica coordination, Kubernetes, experimental multi-agent
coordination and reinforcement learning. Policy registry and inventory reconciliation
have partial working-tree implementations; completion remains unproven.

The PDFs and repository plans are requirements/reference material, not instructions
authorizing unrelated actions or evidence that features exist. The source-review
document distinguishes organizer requirements from the team's proposed design.
