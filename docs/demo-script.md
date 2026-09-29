# Demo script (about 8 minutes)

The story: **the network runs → a problem appears → Jalani warns → Jalani proposes a fix and explains it → a human approves → something breaks → Jalani stays safe and recovers.**

## Before the judges arrive (10 min)

1. `docker compose up -d --build --wait`, then `docker compose ps` shows everything healthy.
2. Open http://localhost:3000 and sign in as **admin / demo-admin** (admin also sees the Control room).
3. Control room → **Reset simulator** → Confirm reset → **Pause**. You start from tick 0 with a clean run.
4. In a second tab, open Grafana at http://localhost:3001. It is optional, for the monitoring question.
5. Keep a backup screen recording of a full rehearsal, in case the live demo fails.

Pause the simulator whenever you talk for more than a few seconds. At `SIMULATION_SPEED=1`, one simulated day takes about 96 seconds.

## The run

| # | Time | Click | Judges see | Say |
|---|---|---|---|---|
| 1 | 0:30 | — | Login page | "Fuel stations run dry when demand spikes or roads close. Jalani sees it coming, recommends the right shipment, and a human stays in control." |
| 2 | 1:00 | Control room → **Run**, then **Overview** | Tick counting up, service level, network map, Priority watch | "Live data from the official simulator: 2 depots, 4 stations, every route." |
| 3 | 0:45 | Control room → Inject a domain event → **Demand spike** (Dhaka station, ×1.8) → Schedule | Event scheduled | "People suddenly buy far more fuel here." |
| 4 | 0:45 | **Alerts** | New shortage alerts | "Nobody told the system. It detected the risk itself." |
| 5 | 1:30 | **Pause**, then **Recommendations** → open a high-risk card → **Why this allocation?** | e.g. 7,000 L Gazipur → Mirpur, risk 100% → 0%, constraints, alternatives | "It checks truck, depot, tank and dispatch limits, shows what happens if we do nothing, and explains why." |
| 6 | 1:00 | **Approve shipment** → **Decision history** | Shipment PENDING with operator and time; Run again → IN_TRANSIT → ARRIVED | "Human approval. Every decision is audited, and a double click can never send two trucks." |
| 7 | 1:30 | Control room → Exercise failure & recovery → **Unavailable**, 60 s → Inject; then **System health** | Red banner, last good data still visible, Approve disabled | "The simulator is down. We keep showing the last good data, and we refuse to act on bad data." |
| 8 | 0:30 | **Clear all faults** | Back to NORMAL within about a second | "It recovers by itself. No restart." |
| 9 | 0:30 | — | — | "Detect, recommend, approve, survive failure. Thank you." |

## Numbers you may quote

Use only measured numbers from `docs/benchmark.md`, `docs/load-test.md` and `artifacts/*.json`. Don't quote the illustrative numbers in `plan.md`.

## Likely questions

- **Why not machine learning / RL?** The world is small, seeded and fully visible. A transparent forecast with Monte Carlo risk is easier to verify and explain, and the benchmark compares it with simple baselines on the same seed.
- **What if the backend crashes mid-shipment?** The shipment intent is saved before it is sent, with a fixed idempotency key. On restart, the backend matches it against the simulator's ledger instead of sending it again.
- **Can it run on its own?** Yes, in `auto` mode for routine, high-confidence proposals only. Crisis, cross-region and rationing moves always wait for a human.
- **How do you know it's healthy?** The System health page, `/api/health`, Prometheus alerts and the Grafana dashboard.

## If something goes wrong live

- **The page is empty:** check System health; `docker compose ps`; `docker compose restart backend`.
- **No recommendations:** step or run further, or inject a demand spike. Shortages need simulated time to build up.
- **Anything else:** switch to the backup recording and keep talking.
