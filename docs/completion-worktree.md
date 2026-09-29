# Advanced completion worktree

Branch: `feature/advanced-completion`.
Location: `.worktrees/advanced-completion` under the original checkout.
The existing uncommitted advanced changes were copied into this worktree on
29 September 2026. Subsequent changes are isolated from the original checkout.

Completed in this worktree:

- Inventory reconciliation requires a complete, deduplicated tick sequence for
  each tank; missing observations for other tanks do not disable valid evidence.
- Rejection cooldown permits review when expected shortage volume increases by
  at least 500 L and 25%, including when stockout probability is saturated.
- Corrected reconciliation fixture arithmetic and policy test isolation.
- Added forecast drift alerts from forecasts issued before observations arrive.
  Repeated reads do not rescore observations or overwrite issued forecasts.
  Sixteen scored residuals are required; absolute mean standardized bias of two
  triggers an alert. State resets with the simulator run and warms up on restart.
  Thresholds are policy choices, not calibrated performance claims.

Still to implement and verify: trained ML forecasting and model selection,
LP/MILP allocation, operations assistant, operator event stream, migrations,
multi-replica coordination, Kubernetes, and experimental coordination/RL.
The imported policy registry and event scoping still require deeper review.
No claim of full goal completion or production deployment is made.
