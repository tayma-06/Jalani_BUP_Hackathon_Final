# Load test

**Result:** 30 concurrent operators, each refreshing the dashboard every second for 80 seconds. Zero failed requests, median response 5.7 ms, p95 132 ms, and the backend used about 30% of one CPU core. All thresholds passed.

We load-test **our** backend, not the organizers' simulator. The backend serves every dashboard read from its cached, validated snapshot, so user traffic never multiplies simulator traffic.

## Setup

| Item | Value |
|---|---|
| Date | 29 September 2026 |
| Target | Full Docker stack (`docker-compose.yml`): nginx frontend → backend (1 Uvicorn worker) → PostgreSQL 16, with the official simulator **running** at speed 1 throughout |
| Path tested | `GET /api/network/state` through the nginx proxy, with a bearer token. This is the dashboard's main 2-second poll and the largest response (~6.6 KB/request average) |
| Tool | k6 (`grafana/k6` image) on the stack's Docker network, script `loadtest/dashboard.js` |
| Load profile | Ramp to 10 users over 20 s, to 30 users over 40 s, then back to 0 over 20 s. Each user makes one request per second |
| Thresholds | p95 < 500 ms, failure rate < 1%, checks > 99% |
| Machine | Intel i7-14650HX (16 cores / 24 threads), 15.7 GB RAM, Windows 11, Docker Desktop (24 CPUs / 7.6 GB available to Docker) |
| Command | `docker run --rm --network jalani_default -v "$PWD/loadtest:/scripts:ro" -v "$PWD/artifacts:/out" -e BASE_URL=http://frontend -e SMOKE_USER=operator -e SMOKE_PASSWORD=demo-operator -e OUTPUT_FILE=/out/load-dashboard.json --user root grafana/k6 run /scripts/dashboard.js` |
| Raw data | `docs/evidence/load-dashboard.json` (copied from `artifacts/`, login token removed) |

## Results

| Metric | Value |
|---|---:|
| Requests | 1,172 in 80.6 s (14.5 req/s at the 30-user plateau) |
| Failed requests | **0 (0.00%)** |
| Checks passed (HTTP 200 + stations present) | 2,342 / 2,342 |
| Median latency | 5.7 ms |
| Average latency | 29.5 ms |
| p95 latency | **131.6 ms** (threshold 500 ms ✓) |
| p99 latency | 245.6 ms |
| Max latency | 304.8 ms |

### Resource use during the test (docker stats, 20 samples at 3 s intervals)

| Container | CPU avg | CPU max | Memory |
|---|---:|---:|---:|
| backend | 30.3% | 38.2% | 220 MiB |
| db (Postgres) | 3.9% | 5.6% | 182 MiB |
| simulator | 9.2% | 15.2% | 103 MiB |
| frontend (nginx) | 0.7% | 3.1% | 24 MiB |

CPU % is relative to one core.

## Bottleneck analysis

- **The median is fast, and the tail is not.** Half the requests finish in under 6 ms, but p95 is about 23× the median. This fits a single-worker backend where a read sometimes waits behind the once-per-second refresh cycle. That cycle's own p95 was about 0.46 s in Prometheus (`decision_cycle_seconds`) at that point in the run, and the heavy forecasting runs in a worker thread that shares Python's GIL with request handling. This is our reading of the numbers; we did not profile to confirm it.
- **It is not CPU-starved.** The backend peaked at 38% of one core, so tail latency comes from contention inside one process, not from exhausted hardware.
- **The database is not the bottleneck** (under 6% CPU). Reads come from the in-memory snapshot, not from queries.
- **Next steps if more headroom were needed:**
  1. Move the forecast and Monte Carlo step into a separate process, so request handling never shares the GIL with it.
  2. Serve `/api/network/state` as a pre-serialized cached JSON body.
  3. Only then consider more workers. That needs a DB-backed execution lease, because approvals are serialized by an in-process lock today.

## Limitations

- One run, on a developer laptop. Docker Desktop on Windows adds virtualization overhead compared with a Linux host.
- Only the read path was load-tested. Approvals are deliberately serialized, and one human operator approves at a time, so they were exercised functionally (`scripts/e2e_drill.py`, backend tests) rather than under load.
- 30 users at 1 request/s is a realistic control-room load, not a maximum-throughput test. We did not push to the breaking point.
