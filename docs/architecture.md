# Architecture

Jalani is a control room for the organizers' fuel-supply simulator. It reads the simulated network, forecasts demand, estimates stockout risk, proposes valid shipments with reasons, and sends a shipment to the simulator only after an operator (or a restricted autopilot) approves it. When a dependency fails it keeps showing the last good data and stops sending shipments.

## Components

```
                 ┌──────────────────────── Docker Compose (one host) ────────────────────────┐
 Browser ──:3000─▶ frontend  (nginx: React build + /api proxy)                               │
                 │     │                                                                    │
                 │     ▼ /api/*                                                             │
                 │  backend  (FastAPI, 1 worker, :8080) ──REST poll + SSE──▶ simulator :8000  │
                 │     │   │                                  (official image, unchanged)  │
                 │     │   └── /metrics ◀── prometheus :9090 ◀── grafana :3001              │
                 │     │        └── alert rules ──▶ alertmanager :9093 ──▶ /api/internal/alerts
                 │     ▼                                                                    │
                 │  db  (PostgreSQL 16; SQLite for local dev/tests)                         │
                 └────────────────────────────────────────────────────────────────────────────┘
```

| Component | Code | Responsibility |
|---|---|---|
| Simulator client | `backend/app/sim/client.py`, `schemas.py` | Typed REST reads, retries with backoff on 5xx only, circuit breaker, stale-header detection, strict schema validation |
| Service loop | `backend/app/service.py` | Refresh on a 1 s poll or an SSE nudge; snapshot consistency check; reset detection; ledger reconciliation; mode (NORMAL / DEGRADED / RECOVERING) |
| Intelligence | `backend/app/intelligence/engine.py` | Forecast, Monte Carlo risk, allocation policy, batch constraint validation, alert detection |
| API | `backend/app/main.py` | JWT roles (viewer / operator / admin), health, metrics, approvals, admin control room |
| Persistence | `backend/app/db.py` | Recommendations, execution intents, demand history, snapshots, alerts, incidents, audit log. Every table is bounded by an explicit retention window applied after each write, so a deployment that runs for months does not grow without limit |
| Frontend | `frontend/src/App.tsx` | Ten pages: overview, stations, recommendations, alerts, supply, forecasts, history, health, control room, audit log |

## Decision pipeline (every refresh)

1. **Read** `/v1/instance`, then regions, depots, stations, routes, supply, events, allocations, metrics and demand history in parallel, then `/v1/instance` again. If the tick moved too far between the two reads, the snapshot is rejected as inconsistent.
2. **Reconcile** every stored execution intent against the simulator's allocation ledger by idempotency key. An intent that has no ledger row, or whose body differs from its row, puts the system into RECOVERING and blocks new shipments.
3. **Forecast** per station and fuel (`profile_v1`). The prior is the documented demand profile × region factor × busy/quiet hour factor. Once there is enough observed history, it switches to learned hourly means, with a smoothed correction from recent observations. Standard deviation is the larger of the profile noise and the observed residual spread.
4. **Risk.** 300 Monte Carlo demand paths over 12 hours, with scheduled arrivals applied before demand. Risk is the share of paths with any unmet demand. The seed is derived from the simulator seed and tick, so counterfactuals share common random numbers.
5. **Allocate** (`greedy_v1`). Candidates are ranked by fuel weight × risk, then by hours to stockout. For each candidate, prefer a route that arrives before the stockout, then a same-region route, then the shortest transit. Ship enough to reach 24 h of cover (12 h when rationing). Each shipment is capped by route limit, depot stock (minus a home-region reserve for cross-region moves), depot dispatch budget and destination tank space, all net of pending and in-transit orders. Every proposal carries before/after risk, half-shipment and do-nothing alternatives, and one alternative route.
6. **Review flags.** Crisis events, cross-region moves, rationing, or a confidence score below 0.75 set `requires_review`. Autopilot never executes these.

## Execution safety

An approval goes through these gates in order. If any gate fails, nothing is sent to the simulator.

1. The DB is reachable, and there is no existing intent for this recommendation (a repeat returns the stored intent).
2. A fresh refresh succeeds and `safe()` holds: a snapshot exists, the DB is OK, the data is not stale, no intent is unresolved, the snapshot is consistent, and its age is ≤ 15 s.
3. A final `/v1/instance` read shows the tick has not moved more than 8 ticks past the snapshot.
4. `validate_batch` passes against the current snapshot.
5. The intent (with immutable key `jal-<run>-<recommendation>`) is **committed to the DB before** the simulator POST.
6. POST `/v1/allocations`. A 4xx response is a definitive FAILED. A timeout or malformed reply is UNKNOWN, which blocks further shipments until the next ledger read resolves it. A retry reuses the original body and key.

A simulator reset (detected by the tick or clock going backwards, a changed identity, ledger rows disappearing, or an SSE reset notice) starts a new `run_id`. Intents left over from the old run are marked `ABANDONED_RESET` and are never replayed.

## Failure behavior

| Failure | Detected by | Response | Operator sees |
|---|---|---|---|
| Simulator unavailable / 5xx | Retries exhausted, circuit breaker | DEGRADED, cached reads, execution blocked | Banner + health page, `sim_up=0` |
| Stale data (`X-Simulator-Stale`) | Response header | DEGRADED, execution blocked | Banner "stale", `state_stale=1` |
| Snapshot too old (> 15 s) | Age check in `safe()` | Execution blocked | Data age on banner |
| Lost POST response | Timeout after POST | Intent UNKNOWN → RECOVERING until the ledger resolves it | "Response uncertain" notice, history row |
| DB down | SQLAlchemy error | DEGRADED before any POST; cached reads stay | Banner "Database unavailable" |
| SSE disconnect | Stream error | REST polling continues; reconnect triggers a full refresh | "REST polling" label |
| Backend crash | Docker health check / restart | Restores state from the DB; reconciles the ledger | Brief interruption |

`/api/health/live` stays 200 while the process runs. `/api/health/ready` returns 503 unless the state is fresh and safe.

## Deliberate choices

- **No reinforcement learning, no separate ML service, no LLM.** The world is small, deterministic by seed and fully observable, so a transparent heuristic plus Monte Carlo risk is easier to verify and explain. `ML_SERVICE_URL` and `LLM_API_KEY` are empty by default, and explanations come from grounded templates.
- **One backend worker.** Execution is serialized by an in-process lock. Scaling out would need a DB-backed execution lease.
- **Single host.** Compose gives a short replacement interruption, not high availability.
