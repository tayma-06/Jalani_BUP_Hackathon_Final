# Policy benchmark

**Question:** Does Jalani's allocation policy (`greedy_v1`) do better than doing nothing, or than a simple reorder rule, in the official simulator?

**Short answer:**
- Both active policies prevent **all** unmet demand over 3 simulated days, in both scenarios. Doing nothing serves only 27–31% of demand.
- `greedy_v1` achieves this while moving **7–10% less fuel** than the naive rule, which leaves more stock in the depots.
- These scenarios did **not** separate the two active policies on service level. We report that as it is.

## Method

| Item | Value |
|---|---|
| Date | 29 September 2026 |
| Simulator | `asifmahmoud414/bup-fuel-supply-simulator:1.0.0` (`sha256:7067050693f4…`), scenario `baseline` v1.0, seed 12345, 15-minute ticks |
| Horizon | 288 ticks = 3 simulated days, from a fresh reset (the last planned supply arrives at tick 212) |
| Stack | Isolated simulator (port 18100) and backend (port 18180, SQLite, background polling off). Never the demo stack. |
| Machine | Intel i7-14650HX (16 cores / 24 threads), 15.7 GB RAM, Windows 11 + Docker Desktop |
| Command | `python scripts/benchmark.py --base http://127.0.0.1:18180 --simulator http://127.0.0.1:18100 --ticks 288 --scenario <baseline\|stress> --allow-reset` |
| Raw data | `docs/evidence/benchmark-baseline.json`, `docs/evidence/benchmark-stress.json` (copied from `artifacts/`) |

Each policy:
1. Resets the simulator to the same seed and schedules the same events.
2. Steps one tick at a time.
3. After each tick, the backend runs a full decision cycle, and the benchmark approves **every** proposal (full policy adherence, no human filtering).

Proposals still go through all of the backend's execution gates. A proposal that has become invalid (for example, depot stock already used by an earlier approval in the same tick) is counted as *blocked*, not forced through.

Scores come from the simulator's own `/v1/metrics`, not from Jalani's model. The first 8 ticks served exactly the same litres under every policy, which confirms that demand is identical across resets (common random numbers).

Policies:
- **do_nothing:** never ships.
- **naive_reorder:** when a station's tank is below 40% full, fill it up, using same-region routes only.
- **greedy_v1:** Jalani's policy (see [architecture.md](architecture.md#decision-pipeline-every-refresh)). It ranks by forecast stockout risk and ships to reach 24 h of forecast cover, within all constraints.

## Results

### Baseline scenario (no events)

| Policy | Service level | Unmet demand | Fuel shipped | Shipments | Blocked by gates |
|---|---:|---:|---:|---:|---:|
| do_nothing | 30.7% | 193,586 L | 0 L | 0 | 0 |
| naive_reorder | 100.0% | 0 L | 297,971 L | 70 | 0 |
| **greedy_v1** | **100.0%** | **0 L** | **269,033 L** | 72 | 0 |

### Stress scenario (the same four events for every policy)

- Gazipur depot supply arrivals × 0.5, ticks 1–288.
- Dhaka demand × 1.8, ticks 24–96.
- Route Gazipur → Mirpur disrupted, ticks 48–96.
- Chattogram demand × 1.5, ticks 120–168.

| Policy | Service level | Unmet demand | Fuel shipped | Shipments | Blocked by gates |
|---|---:|---:|---:|---:|---:|
| do_nothing | 26.5% | 237,893 L | 0 L | 0 | 0 |
| naive_reorder | 100.0% | 0 L | 330,136 L | 79 | 2 |
| **greedy_v1** | **100.0%** | **0 L** | **306,995 L** | 84 | 4 |

### Unmet demand over time: do_nothing (both active policies stay at 0)

| Tick (day) | 48 (0.5) | 96 (1) | 144 (1.5) | 192 (2) | 240 (2.5) | 288 (3) |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 0 | 11,245 | 51,835 | 100,452 | 144,880 | 193,586 |
| Stress | 343 | 37,362 | 88,911 | 144,759 | 189,188 | 237,893 |

## Interpretation

- **Acting matters enormously.** Without shipments, stations start running dry within the first simulated day, and 69–73% of demand goes unserved by day 3.
- **Efficiency is where `greedy_v1` wins.** It meets the same demand with 28,938 L (baseline) and 23,141 L (stress) less fuel moved: 9.7% and 7.0% less. The difference stays in the depots as a buffer against the finite supply schedule and later disruptions. It gets there with slightly more, smaller shipments, because it sizes each one to forecast need instead of filling tanks.
- **Service level did not separate the active policies.** The naive rule, approved without delay every tick, also kept every station supplied, even with half of Gazipur's supply gone. The network has enough total fuel over 3 days, so "fill every low tank" is enough when execution is instant and perfect.
- **Blocked approvals are the safety gate working.** Under stress, 4 of greedy's proposals were invalid by the time they were approved (stock or dispatch already committed that tick), so they were refused instead of sent. None failed at the simulator (`allocation_failures = 0`).

## Limitations

- The benchmark uses one seed and two hand-designed scenarios. It is not a statistical study.
- Operator behavior is idealized: every proposal is approved instantly. Real review delays would favor the policy that plans ahead (`greedy_v1` targets 24 h of cover). This benchmark does not measure that.
- The horizon is 3 days. The scenario's supply is finite, so longer runs eventually force rationing; that regime was not benchmarked.
- Forecast accuracy and risk calibration were not measured separately. Risk values are model estimates.

## Reproduce

```bash
docker run -d --name bench-sim -p 127.0.0.1:18100:8000 -e SIMULATOR_START_MODE=paused \
  asifmahmoud414/bup-fuel-supply-simulator:1.0.0
cd backend && BACKGROUND_ENABLED=false STREAM_ENABLED=false SIMULATOR_URL=http://127.0.0.1:18100 \
  DATABASE_URL=sqlite:///./bench.db uvicorn app.main:app --port 18180 &
cd ../scripts && python benchmark.py --base http://127.0.0.1:18180 --simulator http://127.0.0.1:18100 \
  --ticks 288 --scenario stress --allow-reset --output ../artifacts/benchmark-stress.json
```

On Windows, a TLS-scanning antivirus may drop large local HTTP responses. The script only reads small responses, and it retries only calls that are safe to repeat.
