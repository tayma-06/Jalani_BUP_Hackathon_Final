# prompts.md — copy-paste prompts for your AI coding agent

Updated 29 September 2026. These prompts build the application; the attached pack supplies the CI/CD integration layer.

Works with Claude Code, Cursor, Copilot agent mode or any agent that can read files and run commands in your repo.

## Before the first prompt
1. Repo root contains `plan.md`, `whatwillbedone.md`, `prompts.md`.
2. Put the two PDFs in `docs/` as `docs/challenge-brief.pdf` and `docs/simulator-guide.pdf`.
3. Windows: clone the repo inside WSL (`~/jalani`), open it from WSL in VS Code, Docker Desktop running with WSL integration ON.

## Rules for using these prompts
- **One bounded task per coding session.** Use order 0 → 1 → 13A → 2–8 → 13B → 12 → 13C/13D → 14. Extras 9–11 follow when core gates pass (a small benchmark from 10 should be done before demo).
- Before: `git pull`, clean working tree. After: run the checks in `whatwillbedone.md`, then `git add -A && git commit -m "step N: <what>"`.
- New chat per prompt when the conversation gets long — the agent re-reads `plan.md` and `CLAUDE.md`.
- Team in parallel: each person runs only the prompts for their folder (backend/, frontend/, …). Same-folder work = same person.
- Something broke → Helper **H1** with the *full* error. Don't understand something → **H2** (you must be able to explain it to judges).
- Replace only text in `<ANGLE_BRACKETS>`.

---

## Agent rulebook → Prompt 0 saves this as `CLAUDE.md` (copy it to `AGENTS.md` / `.cursorrules` if you use other tools)

```text
You are the coding agent for our hackathon project "Jalani" (BUP CSE Fest 2026 Hackathon Finals).
Source of truth: plan.md (architecture, names, ports, API contract, phases; Appendix A = simulator API facts).
Also read: whatwillbedone.md (expected outcome of each step), docs/challenge-brief.pdf, docs/simulator-guide.pdf.

Rules:
1. Never modify, rebuild or patch the simulator image. Talk to it only over HTTP at SIMULATOR_URL.
2. Never hardcode station/depot/route/region IDs or world numbers in logic. Discover them from the API.
   (Documented demand priors live only in backend/config/profiles.yaml; scenario YAML files may name IDs.)
3. Execution must stop on stale/invalid data, unresolved writes, or database failure, including manual approvals. Persist the immutable allocation body and idempotency key before POST; reconcile UNKNOWN outcomes after restart.
   All configuration comes from environment variables. Document every new variable in .env.example. Never commit secrets.
4. Stack: Python 3.11, FastAPI, Pydantic v2, httpx (async), SQLAlchemy 2.0 + PostgreSQL, pytest, respx, ruff.
   Frontend: React + Vite + TypeScript + Tailwind + TanStack Query + Recharts. Observability: prometheus_client,
   prometheus-fastapi-instrumentator, Prometheus, Grafana. Load test: k6. ML: scikit-learn, pandas, joblib.
5. We are beginners: simple, readable code. Small functions, descriptive names, short comments explaining WHY.
   No clever one-liners, no unnecessary abstractions or design patterns.
6. Test consequential behavior: allocation constraints, duplicate approval, durable idempotency, invalid/stale data, recovery, and permissions. Avoid tests that merely mirror trivial implementation. Every important action logs one JSON line (event names in plan.md §8.4).
   Add the Prometheus metrics that plan.md §8.1 lists for the code you touch.
7. Only touch the folders the current prompt names. Don't refactor unrelated code. Don't rename anything defined in plan.md.
8. `docker compose up -d` must keep working after every step. Every Docker healthcheck must use a tool that exists
   in that image (slim Python images have no curl — use python; alpine images have wget).
9. If something is ambiguous, choose the simplest option consistent with plan.md and list your assumptions at the end.
   If plan.md is clearly wrong, say so and propose the fix instead of silently diverging.
10. When done: run the tests/build yourself, then reply with (a) files created/changed, (b) exact commands + URLs to verify
    and what I should see, (c) assumptions. Tick the matching boxes in whatwillbedone.md and add a 1–3 line note
    under that step.
11. Explain anything non-obvious in plain English in your final message — we must present this to judges.
```

---

## Prompt 0 — Kickoff (no app code) · [MVP] · all

```text
Read plan.md fully, then whatwillbedone.md, and skim docs/challenge-brief.pdf and docs/simulator-guide.pdf.
Do NOT write application code yet.

1. Create CLAUDE.md in the repo root containing the "Agent rulebook" block from prompts.md, copied exactly.
2. Reply with:
   a) a 10-line summary of what we are building and the 8-step loop, in simple words
   b) the services and ports you will create
   c) anything in plan.md that looks contradictory, risky or unclear (max 8 bullets)
   d) one line each on how you'll approach Phases 1, 2 and 3
Keep it short. Then stop and wait for Prompt 1.
```

---

## Prompt 1 — Repo skeleton + simulator + exploration · [MVP] · A

```text
Phase 1. Read plan.md §3, §4, §11, §16, §18 and Appendix A. Work only in: repo root files, scripts/, docs/, data/.

Build:
1. The folder structure from plan.md §18 (put a short README.md in each empty folder saying what goes there).
2. .gitignore (Python, Node, .env, node_modules, dist, __pycache__, .pytest_cache, data/*.csv) and
   .gitattributes forcing LF line endings.
3. .env.example with every variable we'll need, grouped and commented: SIMULATOR_URL=http://simulator:8000,
   SIMULATION_SPEED=1, TICK_MINUTES=15, SIMULATOR_START_MODE=paused, POSTGRES_USER/PASSWORD/DB, DATABASE_URL,
   JWT_SECRET, VIEWER_USER/PASSWORD, OPERATOR_USER/PASSWORD, ADMIN_USER/PASSWORD, ML_SERVICE_URL=http://ml-service:8001,
   LLM_BASE_URL, LLM_API_KEY (empty), LLM_MODEL, CHAOS_ENABLED=false, APP_VERSION=0.1.0, GIT_SHA=dev (release images use immutable digests),
   GRAFANA_ADMIN_PASSWORD. Use obviously fake demo passwords.
4. docker-compose.yml with, for now, two services on one network:
   - simulator: image asifmahmoud414/bup-fuel-supply-simulator:1.0.0, env SIMULATION_SPEED / TICK_MINUTES /
     SIMULATOR_START_MODE from .env, port 8000, restart unless-stopped, and a healthcheck on /v1/health.
     First inspect the image (docker run --rm --entrypoint sh <image> -c "which curl wget python python3") and
     write a healthcheck command that actually exists in the image.
   - db: postgres:16-alpine, named volume, healthcheck with pg_isready, no host port.
   Later phases add the other services.
5. Makefile: up, down, ps, logs, explore (more targets come later).
6. scripts/explore.py (httpx; SIMULATOR_URL env, default http://localhost:8000), readable table output:
   - instance, regions, depots, stations, routes, supply arrivals (sorted), events, metrics
   - daily demand per station/fuel from the documented profiles in plan.md Appendix A.7 (labelled "documented
     estimate"), hours of cover per station/fuel, depot days of cover for its home region
   - total supply per depot/fuel vs 3-day demand → print clearly whether the network is supply-constrained
   - the supply window: last planned_tick in /v1/supply-arrivals (after it no new fuel arrives) and roughly how
     many sim days all the fuel (depots + stations + scheduled supply) lasts against total demand
   - saves everything to data/world_snapshot.json
   - flag --probe-dispatch: on a freshly reset + paused simulator, create 1,000 L allocations to one station
     (idempotency keys "probe-<n>") until DISPATCH_CAPACITY_EXCEEDED, then /admin/step and try again, to find out
     whether IN_TRANSIT allocations still count toward dispatch_capacity_per_tick. Also replay one key with the same
     body (200 or 201?), then keep stepping until the shipments arrive and report what happens when they overfill
     the station (capped at capacity? extra lost? over capacity?). Print the conclusions, then /admin/reset.
   - flag --demand-sample N: step N ticks while paused, read /v1/demand-history, and compare with
     profile × hour factor × region factor to check whether the region demand_factor is applied and which
     order demand-history rows come back in. Then /admin/reset.
7. docs/assumptions.md: start with plan.md §16, then a section "Verified in Phase 1" answering every
   question in plan.md §4 with evidence from the script output.
8. scripts/requirements.txt (httpx) and README.md: short quick start (clone inside WSL home, cp .env.example .env,
   docker compose up -d, python3 -m venv .venv && source .venv/bin/activate && pip install -r scripts/requirements.txt,
   URLs table).

Actually run it: docker compose up -d simulator db, explore.py normally and with both flags, reset the simulator
at the end. Report the answers to the plan.md §4 questions in your final message.
```

---

## Prompt 2 — Backend foundation · [MVP] · A

```text
Phase 2. Read plan.md §2, §6.1, §6.2, §6.4, §6.5 and Appendix A, plus docs/assumptions.md.
Work only in backend/ and docker-compose.yml.

Build a FastAPI service "backend" on port 8080:
1. app/config.py — pydantic-settings; every value from env.
2. app/sim/schemas.py — Pydantic models for every /v1 response in Appendix A (extra="ignore"; enums for statuses
   and fuel types). Include the admin responses we use.
3. app/sim/client.py — one shared httpx.AsyncClient:
   - timeouts: connect 2 s, read 5 s
   - retry with exponential backoff + jitter (max 3 attempts) ONLY for GETs and POST /v1/allocations
     (safe because of idempotency keys); never retry 4xx
   - parse all three error shapes (detail{code}, error{code}, detail[...] for 422) into SimError(status, code, message)
   - read X-Simulator-Stale and return it with the data
   - Prometheus: sim_requests_total{endpoint,status}, sim_request_duration_seconds{endpoint}
   - admin helpers (run, pause, step, reset, create_event, create_fault, clear_faults) without retries
4. app/core/state.py — StateStore: latest snapshot, last-known-good snapshot, fetched_at, data_age, stale flag,
   run_id. Detect simulator reset (tick decreased) → new run_id + log sim.reset_detected.
5. app/ingest.py — background asyncio task started on app startup: every 1 s check /v1/instance; even if the tick is unchanged, refresh everything in parallel (instance, regions, depots, stations, routes, supply-arrivals, events,
   allocations, metrics, demand-history with limit = 12 × ticks since last fetch + 24, max 2000 — use the
   ordering finding from docs/assumptions.md). Coalesce: never two refreshes at once, always use the latest.
   Validation failure → keep previous snapshot, log integration.error.
6. app/db — SQLAlchemy 2.0 models + session for every table in plan.md §6.4 (create tables on startup, no Alembic).
   Upsert demand history by (run_id, simulator id) and store each station's demand_multiplier at that tick.
   Persist a snapshot every 4 ticks.
7. API under /api: GET /health/live (process up), /health/ready (DB reachable AND initialized/reconciled state AND a sufficiently fresh, non-stale validated snapshot exists), /health
   (components backend, database, simulator; other components "not_configured" for now). The simulator component
   is healthy only if the last /v1/* refresh succeeded recently — NOT just /v1/health, which bypasses injected
   faults and says "ok" while /v1/* is failing. GET /network/state in exactly the shape of plan.md §6.5
   (risk/forecast/demand fields null for now), GET /supply, GET /events.
   GET /metrics via prometheus-fastapi-instrumentator.
8. JSON logging (python-json-logger); include tick and run_id on every line.
9. Dockerfile (python:3.11-slim, non-root user), pinned requirements.txt. Add "backend" to docker-compose.yml:
   port 8080, env_file .env, depends_on db (service_healthy) and simulator (service_started only — the backend must
   start and show degraded mode even when the simulator is down), healthcheck on /api/health/live using python
   (the image has no curl), restart unless-stopped.
10. tests/: respx-mocked client tests (retry on 503, no retry on 409, all error shapes, stale header),
    schema tests using the example JSON from Appendix A, reset-detection test, network-state shape test.

Verify: docker compose up -d --build; open localhost:8080/docs; POST localhost:8000/admin/run; call
/api/network/state twice and show inventories changing; POST /admin/pause; run pytest inside the container.
```

---

## Prompt 3 — Frontend MVP · [MVP] · C

```text
Phase 3. Read plan.md §6.5 (API contract) and §7. Work only in frontend/ and docker-compose.yml.

Create a React + Vite + TypeScript app with Tailwind:
1. src/api/: typed client for GET /api/network/state and GET /api/health (types match plan.md §6.5 exactly).
   TanStack Query with refetchInterval 2000 and placeholderData = previous data. If a request fails, keep
   showing the last data with an "Offline since HH:MM:SS" chip — never blank the screen.
2. Mock mode: when VITE_USE_MOCKS=true, return the example JSON from plan.md §6.5 (extend it to all 4 stations,
   2 depots, 6 routes) so the UI can be built before the backend is ready.
3. Layout: left nav (Overview, Stations & Depots, Recommendations, Alerts, Supply & Disruptions, Forecasts,
   Decision History, System Health, Control Room — unfinished pages show "Coming in step N"); top bar with sim tick,
   sim time, RUNNING/PAUSED, mode chip (NORMAL/DEGRADED/RECOVERING) and a "SIMULATED ENVIRONMENT" badge;
   full-width banner when mode != NORMAL or stale = true; footer with version.
4. Overview: KPI cards (service level %, unmet litres, in-transit litres, open alerts, pending recommendations);
   SVG network schematic built from the routes list (depots left, stations right, route lines coloured by status,
   cross-region routes dashed, station nodes coloured by worst risk level, grey when unknown) — no hardcoded IDs;
   "Top risks" list with an empty state.
5. Stations & Depots: one card per station with 3 fuel bars (inventory vs capacity, % label, colour by fill),
   status badge, demand multiplier; depot cards with inventory bars and dispatch used / capacity.
6. Visual style: clean control-room look, big numbers, green OK / amber warning / red critical / grey unknown.
   Must look good on a 1366×768 laptop and not break on a phone.
7. Dockerfile: multi-stage (node:20-alpine build → nginx:alpine). nginx.conf serves the SPA (fallback to
   index.html) and proxies /api to http://backend:8080. Add "frontend" to docker-compose.yml on port 3000
   with a healthcheck.

Verify: npm run build passes; docker compose up -d --build frontend; open localhost:3000 with the simulator
running and watch the bars move; docker compose stop backend → offline chip appears and last data stays;
start backend again.
```

---

## Prompt 4 — Intelligence v1a: forecast, risk, detection, alerts · [MVP] · B

```text
Phase 4. Read plan.md §5.1, §5.2, §5.3 and Appendix A.7. Work in backend/app/intelligence/, backend/app/api/,
backend/config/, backend/tests/, and the frontend pages Stations & Depots, Overview (Top risks), Alerts, Forecasts.

1. backend/config/profiles.yaml — documented demand profiles, hour-of-day factors and noise from Appendix A.7,
   with a comment: "priors from the organizer guide, used only for cold start".
2. forecast.py — BaselineForecaster (model_version "profile_v1"):
   - learns mean base demand per (station, fuel, hour_of_day) from demand_obs (demand ÷ multiplier at that tick)
     over the last 3 sim days; uses the prior for any (station, fuel, hour) with < 2 samples
   - forecast(station, fuel, start_tick, horizon) → per-tick list = base × current demand_multiplier × correction,
     correction = EWMA(alpha 0.3) of actual/forecast over the last 8 ticks, clipped to [0.5, 2.0]
   - residual_std(station, fuel) from recent residuals (prior noise × mean when data is short)
   - export forecast_mae{model="profile_v1"} (rolling live MAE)
3. risk.py:
   - inventory_position(): on-hand + all simulator PENDING/IN_TRANSIT allocations to that station/fuel with their arrival
     ticks (PENDING arrives at created_tick + 1 + transit_ticks; IN_TRANSIT at expected_arrival_tick)
   - project(): deterministic walk for H = 48 ticks → hours_to_stockout (None if no stockout within H)
   - stockout_risk(): numpy Monte Carlo, N = 300 paths, normal noise with residual_std, seedable →
     probability of hitting zero within H + expected unmet litres
   - risk_level() thresholds from plan.md §5.2 (CRITICAL also when stockout comes before the fastest AVAILABLE
     route could deliver)
4. detection.py — every detector in the plan.md §5.3 table. Each returns an Alert(type, severity, entity_type,
   entity_id, fuel, message, evidence). Cross-check with ACTIVE /v1/events and append "confirmed by event #id".
   Remember first-seen supply quantities and planned ticks (DB) to detect delays and shortfalls.
5. Alerts in DB with lifecycle OPEN → ACKNOWLEDGED → RESOLVED (auto-resolve when a detector stops firing for
   4 ticks). Metric shortage_alerts_total{severity}. Logs alert.opened / alert.resolved.
6. Pipeline: after each fresh snapshot run forecast → risk → detection (max once per second); fill forecast_12h,
   hours_to_stockout, risk, risk_level, kpis.open_alerts and regions[].fuels (demand in the last hour vs normal
   for that hour, 12 h forecast) in /api/network/state.
   New endpoints: GET /api/stations/{id}/forecast?fuel=, GET /api/alerts?status=, POST /api/alerts/{id}/ack
   (auth comes in step 6 — leave a TODO dependency).
7. Frontend: hours-to-stockout + risk badge per fuel on Stations; Top risks and a Regional demand panel (per region
   and fuel: last hour vs normal, arrow up/down) on Overview; Alerts page (filters by severity/type, acknowledge button); Forecasts page with a Recharts line (history + forecast + ±2σ band) and
   a station/fuel picker.
8. Tests: projection with hand-calculated numbers; Monte Carlo sanity (huge stock → risk ≈ 0, empty with positive unmet demand → risk ≈ 1; zero demand need not imply a stockout);
   each detector on a tiny fake snapshot; forecaster cold start vs learned.

Verify: run the simulator at speed 1 for ~5 sim hours: Tongi DIESEL hours-to-stockout falls and risk rises.
Then POST /admin/events {"type":"demand_spike","start_tick":<now+1>,"duration_ticks":24,
"parameters":{"region_ids":["region-dhaka"],"multiplier":1.8}} and show the anomaly alert appearing.
```

---

## Prompt 5 — Intelligence v1b: recommender, what-if, confidence, explanations · [MVP] · B

```text
Phase 5. Read plan.md §5.4–§5.7 and the recommendation example in §6.5, plus docs/assumptions.md
(dispatch-capacity finding). Work in backend/app/intelligence/, backend/config/policy.yaml, backend/app/api/,
backend/tests/.

1. policy.yaml: trigger (hours_to_stockout < 12 or risk ≥ 0.20), target_cover_hours 24, rationing_cover_hours 12,
   min_shipment_liters 500, fuel_priority {DIESEL 1.3, PETROL 1.0, OCTANE 0.8}, cross_region_reserve_hours 12,
   constrained_dispatch_factor 0.5, confidence thresholds. Loaded at startup; reloadable.
2. recommender.py — GreedyPolicy ("greedy_v1") implementing plan.md §5.4 steps 1–6 exactly. It must NEVER
   produce an allocation that breaks: route status, station status, depot status, route.max_shipment (split into
   parts), depot inventory (incl. cross-region reserve), depot dispatch headroom (this cycle + existing
   PENDING/IN_TRANSIT per docs/assumptions.md), station headroom (capacity − on-hand − PENDING − IN_TRANSIT − reservations already made in this plan).
   Record every limit that reduced the quantity as a readable constraint string.
   Also implement NaiveReorderPolicy (plan.md §9) — it is the last-resort fallback when no forecast exists.
3. whatif.py: Monte Carlo with and without the shipment using the SAME random seed → risk_before/after and
   unmet_before/after; alternatives: half quantity, best other depot/route (if any), do nothing.
4. confidence.py: plan.md §5.6 → score, level, reasons. requires_review = level LOW or data stale or mode != NORMAL.
5. explain.py: template explanation from signals/constraints/impact/confidence, e.g.
   "Mirpur DIESEL runs out in ~6.2 h (risk 72%). Sending 5,000 L from Gazipur (arrives in ~45 min) cuts risk to 19%.
   Limited by Gazipur's dispatch capacity this tick."
6. Store recommendations (status PROPOSED). Each cycle replace PROPOSED ones for the same station+fuel;
   mark unexecuted ones older than 4 ticks EXPIRED. Metrics recommendations_total{status},
   recommendation_confidence, decision_cycle_seconds.
7. API: GET /api/recommendations?status=, POST /api/decide?dry_run=true (compute now, return cards, write nothing).
   Response shape = plan.md §6.5 card.
8. Tests (the most important in the project): randomised small worlds proving no limit is ever broken;
   disrupted home route → cross-region route chosen; scarce depot → higher weight wins and rationing lowers
   targets; split when > max_shipment; risk_after ≤ risk_before; naive policy only valid moves.

Verify: with the simulator running, GET /api/recommendations shows cards; inject a route_disruption on
route-gazipur-mirpur and show new Mirpur recommendations using route-patiya-mirpur.
```

---

## Prompt 6 — Decision workflow · [MVP] · A + C

```text
Phase 6. Read plan.md §5.8, §6.3, §6.5 and §7. Backend: app/decisions/, app/auth.py, app/api/.
Frontend: login, Recommendations page, Decision History page, autopilot switch.

Backend:
1. auth.py: POST /api/auth/login with the three users from env (viewer/operator/admin); JWT HS256 with
   JWT_SECRET, 8 h expiry; dependency require_role(). Reads need viewer+, decisions operator+, control admin.
   Protect every existing endpoint accordingly (health and /metrics stay public).
2. executor.py: approve(rec_id, quantity?, user) → re-check against the latest state and all limits (re-run
   what-if if the quantity changed) → transactionally persist immutable bodies and keys, then POST /v1/allocations for each part with key jal-{run_id}-{rec_id}-{part}
   → save executions → status SUBMITTED. Map every response exactly as the plan.md §6.3 table. Retries reuse the
   same key. Accept both 200 and 201 for replays. Once a key has been sent, that body is frozen: Modify is only
   allowed before the first send (a new body under an old key = 409 IDEMPOTENCY_KEY_MISMATCH).
3. Tracking: on each refresh, update executions from /v1/allocations (PENDING/IN_TRANSIT/ARRIVED/FAILED/CANCELLED)
   and set the recommendation status accordingly. Persist an UNKNOWN state for lost responses; never assign a new key until the old outcome is reconciled. Cancel for PENDING shipments (operator).
4. autopilot.py: ADVISORY (default) / AUTOPILOT stored in the settings table; gates from plan.md §5.8; never
   runs while mode != NORMAL or data is stale. Metrics decisions_executed_total{mode},
   human_review_requests_total. Logs decision.approved/rejected/executed/failed.
5. audit_log row for login, approve, modify, reject, cancel, autopilot toggle, policy change.
6. Endpoints: POST /api/recommendations/{id}/approve {quantity?}, POST /api/recommendations/{id}/reject {reason},
   POST /api/allocations/{sim_id}/cancel, GET /api/decisions (recommendation + executions + outcome),
   GET/POST /api/settings/autopilot.

Frontend:
7. Login page; token kept in memory + sessionStorage; role shown in the top bar; actions the role can't do are
   hidden or disabled with a tooltip.
8. Recommendations page: cards laid out like the brief's example (ALERT header; station; fuel; projected
   stockout; current inventory; expected demand; recommended allocation; expected result risk before → after),
   confidence pill, "Needs review" tag, expandable "Why?" (signals, constraints, alternatives table, explanation +
   source label), buttons Approve / Modify (number input with live max hint) / Reject (reason).
   Buttons disabled while a request is in flight (no double submits).
9. Decision History page: table (tick, time, station, fuel, qty, route, decided by, status chips
   PENDING → IN_TRANSIT → ARRIVED / FAILED, failure reason), filters, CSV export.
10. Autopilot switch in the top bar (operator+) with a confirm dialog explaining the gates.

Tests: executor error mapping (respx), double approve → one allocation, role checks.

Verify: approve a card → it appears at localhost:8000/v1/allocations and reaches ARRIVED; double-click approve →
still one allocation; viewer can't approve; autopilot ON → HIGH-confidence recs execute, LOW ones wait.
```

---

## Prompt 7 — Live stream + resilience + Control Room · [MVP → STRONG] · A

```text
Phase 7. Read plan.md §2, §6.1, §10 and Appendix A.4–A.5. Backend: app/sim/stream.py, app/core/, app/api/control.py.
Frontend: banners + Control Room page. Docs: docs/runbook.md.

1. stream.py: SSE client for /v1/stream. Read the raw stream line by line (httpx stream + aiter_lines) and parse
   "event:"/"data:" yourself — SSE libraries usually hide ": keepalive" comments, which the watchdog needs.
   The handler ONLY wakes the refresher (the simulator drops subscribers that fall 200 events behind).
   Watchdog: no lines at all for 20 s (an idle stream still sends a keepalive every 15 s) → reconnect.
   Every (re)connect → full REST resync. Use a stream-specific timeout longer than the 15 s keepalive (do not reuse a 5 s JSON read timeout). On 503 → exponential backoff + jitter (max 30 s) while polling every 1 s.
   Metric sim_stream_connected; health component event_stream = connected | polling.
2. breaker.py: circuit breaker around the /v1/* data calls (5 consecutive failures, each counted only after its
   retries are exhausted → OPEN 10 s → HALF_OPEN: one probe GET /v1/instance → CLOSED on success).
   Do NOT probe with /v1/health — it bypasses injected faults, so it would "recover" while /v1/* is still down.
   /admin/* calls skip the breaker (they bypass faults too) so the Control Room works during an outage.
   Metric sim_circuit_state; logs on every transition.
3. mode.py: NORMAL / DEGRADED (breaker open, data older than 10 s, or stale) / RECOVERING (first good resync) →
   NORMAL. Log mode.changed, metric system_mode. In DEGRADED: serve last-known-good with data_age, pause autopilot,
   mark proposals as reviewable but BLOCK both manual and automatic allocation writes until validated data and durable DB storage recover.
4. Stale data: X-Simulator-Stale → state.stale = true; keep the data but flag it; clear after 3 clean responses.
5. Invalid-data guard beyond Pydantic: no negative inventory, inventory within documented bounds; internally consistent discovered IDs; monotonic tick except a reset. Do not reject a valid large tick gap after downtime merely because it exceeds a magic number → reject snapshot, keep previous, raise SYSTEM alert "INVALID_SIMULATOR_DATA".
6. Control Room API (admin only, all audited): POST /api/control/sim/{action} for run/pause/step/reset,
   POST /api/control/events (validated body), POST /api/control/faults, POST /api/control/faults/clear,
   GET lists of /admin/events and /admin/faults.
   Do not expose simulator/admin ports publicly. Chaos, only when CHAOS_ENABLED=true: POST /api/control/chaos/crash (log, then os._exit(1) so Docker restarts
   it) and POST /api/control/chaos/corrupt-next (next snapshot gets a negative inventory to prove the guard).
7. After a restart: rebuild state from DB + a fresh resync; open recommendations survive, but must be revalidated. Reconcile persisted UNKNOWN executions before any new write. Block writes while DB is down; do not queue unpersisted writes in memory.
8. Frontend banners: red "Simulator unreachable — showing data from tick N (X s old)", yellow "Stale data —
   execution blocked until refreshed", blue "Live stream lost — polling". Control Room page: buttons for sim control; forms for
   the 6 event types and 5 fault types with defaults and dropdowns filled from discovered IDs; demo presets
   ("Demand spike Dhaka ×1.8, 24 ticks", "Route gazipur→mirpur down 16 ticks + Gazipur shipment delay 8",
   "Combined crisis"); chaos buttons; live list of events and faults.
9. docs/runbook.md: one section per row of plan.md §10 (symptom, detection, automatic response, operator action,
   how to verify recovery).
10. Tests: breaker transitions, mode transitions, stale handling, sanity-guard rejection, SSE handler does no
    heavy work (mock).

Verify each and report: fault unavailable 60 s (red banner → auto-recovery); stream_disconnect 60 s (polling chip);
stale_data 60 s (yellow banner + execution blocked); chaos crash (backend restarts, UI recovers); corrupt-next
(alert raised, previous data kept).
```

---

## Prompt 8 — Observability · [MVP] · D

```text
Phase 8. Read plan.md §8. Work in observability/, backend/app/metrics.py, docker-compose.yml and the frontend
System Health page.

1. Add every metric in plan.md §8.1 that doesn't exist yet (note how sim_up is defined there). Domain gauges update
   after each pipeline run.
   Keep labels low-cardinality (station, fuel, depot, model, component, status only).
2. Rolling p95 latency and error rate over the last 5 minutes computed in-process for GET /api/health, so the UI
   works even if Prometheus is down.
3. observability/prometheus/prometheus.yml: scrape backend:8080/metrics and ml-service:8001/metrics every 5 s
   (ml-service may be down for now); rules.yml with the alerts in plan.md §8.3.
4. Grafana: provisioning for the Prometheus datasource and a dashboards provider; three dashboards as JSON
   (Service health, Intelligence, Fuel network) with the panels in plan.md §8.2 plus an alert-list panel.
   Anonymous viewer access only on the local demo profile; admin password from GRAFANA_ADMIN_PASSWORD.
   If a dashboard JSON fails to load, simplify it rather than guessing — we can rebuild in the UI and export.
5. docker-compose: prometheus (9090) and grafana (3001) with volumes, healthchecks, restart unless-stopped.
6. /api/health: all components from the plan.md §8.4 example (prediction_service and llm "not_configured" until
   steps 9 and 11), plus version and git SHA (build args).
7. Frontend System Health page: component table with coloured status, p95 latency, error rate, fallbacks, mode,
   version, data age, links to Grafana and Prometheus.
8. Make sure every log event in plan.md §8.4 exists; add a Makefile target logs-decisions that filters them.

Verify: localhost:9090/targets UP; Grafana dashboards live; latency fault {"delay_ms":800} → p95 panel rises;
unavailable fault → SimulatorDown firing at localhost:9090/alerts. Save screenshots to docs/observability/.
```

---

## Prompt 9 — ML service · [STRONG] · B

```text
Phase 9. Read plan.md §5.1 and §9. Work in scripts/, ml-service/, backend/app/intelligence/forecast.py,
docker-compose.yml and the frontend Forecasts page.

1. scripts/generate_history.py: requires --yes (it resets the simulator). Reset, pause, optionally inject 2–3
   demand spikes at different ticks (--with-events), /admin/step N ticks (default 960 = 10 sim days), pull
   /v1/demand-history per station with limit 2000 every 500 ticks, dedupe by id, record each station's
   demand_multiplier per tick from /v1/stations, save data/demand_history.csv, reset at the end.
   Write docs/data.md explaining exactly how the data was generated (generated, not real).
2. ml-service/train.py: features station, fuel, hour_of_day, demand_multiplier, lag_1, lag_2, rolling_mean_4;
   target demand_liters; time-based split (last 20% = test). Compare the baseline (same idea as the backend
   profile forecaster) vs HistGradientBoostingRegressor: MAE and MAPE per fuel. Save residual std per
   (station, fuel) for uncertainty. Save models/demand_vN.joblib + demand_vN.json (metrics, features, data range),
   update models/registry.json {active, previous}, append to experiments.csv (time, git sha, params, metrics),
   write docs/model-report.md with a table and a PNG chart.
3. ml-service app (FastAPI, port 8001): GET /health, GET /model (active version + metrics),
   POST /predict {station_id, fuel_type, start_tick, horizon_ticks, hour_of_day_start, demand_multiplier,
   recent_demand[]} → {mean[], std[], model_version}; POST /model/activate {version} protected by an admin token
   from env; /metrics (predict latency, requests, errors). Dockerfile + compose service with healthcheck.
   Commit the trained model files so the stack runs without retraining.
4. Backend MLForecaster client: 800 ms timeout + circuit breaker. Champion/challenger: track live MAE of both;
   use ML when healthy and not worse than the baseline by > 10%, else baseline. Fallback → metrics
   fallback_active / fallback_activations_total{component="forecast"}, log fallback.activated, health component
   prediction_service = healthy | fallback | down. Recommendations record model_version.
5. Frontend Forecasts page: model badge (e.g. "ML demand_v2" / "Fallback: profile_v1"), live MAE per model,
   version list; admin button "Activate previous version" (model rollback).
6. Stretch: drift alert when live MAE > 1.5 × validation MAE for 16 ticks.
7. Tests: feature building, /predict schema, backend fallback when ml-service times out (respx).

Verify: numbers in docs/model-report.md; docker compose stop ml-service → Fallback badge and recommendations
continue; start it → ML again; activate the previous version and show /model.
```

---

## Prompt 10 — Benchmark (+ LP optimizer stretch) · [STRONG] · B

```text
Phase 10. Read plan.md §5.4 (v2) and §9. Work in backend/app/benchmark.py, backend/app/intelligence/,
scenarios/, docs/, and a small frontend section.

1. scenarios/*.yaml, each with name, description, ticks (default 288 — if docs/assumptions.md says supply ends
   earlier, note it in the results: after that point every policy runs dry) and events in /admin/events format:
   - calm: no events
   - demand_spike: region-dhaka ×1.8, ticks 20–60
   - route_disruption_delay: route-gazipur-mirpur down ticks 30–70 + shipment_delay 8 ticks at depot-gazipur
   - combined_crisis: all of the above + station_outage station-coxsbazar ticks 80–100 + supply_shortfall
     factor 0.5 at depot-patiya
2. One policy interface decide(state) -> list[allocation]: do_nothing, naive_reorder, greedy_v1 (auto-approve
   everything so the run is hands-free), and lp_v2 if built.
3. `python -m app.benchmark --scenario all --policy all --ticks 288`: asks for confirmation (it resets the
   simulator), pauses our background loop via a settings flag, then for each scenario × policy: reset → pause →
   inject events → loop {refresh state, decide, submit, /admin/step} → read /v1/metrics and count stockout ticks
   from demand history → results CSV. Finally write docs/benchmark-results.md (table + matplotlib chart) and a
   JSON copy for the UI, then resume the loop.
4. Stretch lp_v2 with PuLP + CBC exactly as plan.md §5.4 v2 (split to ≤ max_shipment). Admin policy switch via
   GET/POST /api/settings/policy and a selector in the Control Room = policy rollback.
5. Show the benchmark table on the Decision History page (section "How good are our decisions?").
6. Tests: every policy returns only valid allocations on a fake state.

Verify: run the full benchmark and show the table. If greedy_v1 doesn't beat naive_reorder in some scenario,
explain why in plain words, propose a policy.yaml change, apply it and rerun.
```

---

## Prompt 11 — GenAI incident briefs & summaries · [STRONG] · A or C

```text
Phase 11. Read plan.md §5.9. Work in backend/app/intelligence/llm.py, explain.py, backend/app/api/ and the
frontend Overview + Recommendations "Why?" panel.

1. llm.py: OpenAI-compatible client (openai SDK with base_url=LLM_BASE_URL, api_key=LLM_API_KEY, model=LLM_MODEL).
   Disabled when no key. 8 s timeout, 1 retry, circuit breaker, cache by hash of the input JSON,
   metric llm_requests_total{status}, health component llm = healthy | fallback | disabled.
2. Grounded prompts. System prompt: "You write short operational notes for a fuel operations center. Use ONLY the
   facts in the JSON. Never invent numbers, IDs or actions. If a fact is missing, say it is unknown. This is a
   simulated environment." Input = compact JSON of allowed facts only.
3. Features:
   a) incident brief when a new crisis is detected (route disruption, demand anomaly, outage, supply delay or
      shortfall, or two at once): 3–4 sentences — what happened, impact (stations, fuels, hours to stockout),
      what the system recommends. Stored in incidents; GET /api/incidents.
   b) GET /api/summary: shift summary of the current network.
   c) optional plain-language rewrite of a recommendation's template explanation (on demand, cached).
4. Fallback everywhere: template text with the same facts. Store and show the source ("AI-generated" / "Template").
5. Frontend: incident feed on Overview, "Summarize network" button, AI text in the "Why?" panel with its label.
6. Tests: fallback on no key and on timeout (mocked), prompt contains only allowed facts.

Verify: Control Room → Combined crisis → an incident brief appears; blank LLM_API_KEY and restart backend →
template text appears and nothing breaks.
```

---

## Prompt 12 — Load testing · [MVP] · D

```text
Phase 12. Read plan.md §12. Work in loadtest/, scripts/loadtest_report.py, docker-compose.yml (profile
"loadtest"), Makefile and docs/load-test-results.md.

1. k6 scripts: dashboard.js (GET /api/network/state; login once in setup()), decide.js
   (POST /api/decide?dry_run=true as operator), predict.js (POST ml-service /predict).
   Stages as in plan.md §12; thresholds p(95)<500 and http_req_failed<0.01;
   summaryTrendStats avg,min,med,p(90),p(95),p(99),max; handleSummary writes JSON to loadtest/results/.
2. Compose service k6 (grafana/k6) under profile "loadtest", scripts mounted, targets service names.
   Make targets loadtest-dashboard, loadtest-decide, loadtest-predict, loadtest (all).
3. scripts/loadtest_report.py: turns the JSON summaries into docs/load-test-results.md (table: workload, max VUs,
   avg, p50, p95, p99, req/s, error %, notes) and includes `docker stats --no-stream` captured during each run.
4. Run dashboard and decision dry-run tests; include prediction only if ml-service exists. At least one meaningful workload is required. Run with the simulator RUNNING at speed 1 (realistic). Find the slowest path, explain the bottleneck
   in plain words, apply ONE fix (e.g. cache the computed network state per tick, reuse DB sessions, vectorise
   the Monte Carlo), rerun, add a before/after row.
5. Write the exact reproduce commands in the doc.

Verify: docs/load-test-results.md has real numbers for the implemented workloads plus the before/after comparison.
```

---

## Prompt 13A — Add CI immediately after Prompt 1

```text
Read plan.md §11, CI_CD_GUIDE.md, docs/ci-contract.md, and the supplied .github/workflows/ci.yml.
Start CI now. Inspect the actual repository; the pack contains integration files, not finished application code.
Create/fix real Python dependency lockfiles and frontend package-lock.json. Ensure package.json exposes lint,
typecheck, test:ci, and build. Add meaningful tests for existing behavior. Run the pack's unittest suite.
For an early skeleton, use a clearly named early-CI workflow that checks only implemented components; do not
claim full release readiness. The final release workflow must fail on missing app files/tests, not skip them.
Use PR checks without deployment secrets. Set required status checks when repository settings permit it.
Keep CI configuration under version control and add dependabot for action updates. Use verified action versions;
pin their resolved commit SHAs when enabling the real repository. Never invent hashes.
Verify a deliberately failing test on a disposable branch is red, then repaired is green. Report run links,
what ran, and what remains pending. Do not call the application complete.
```

## Prompt 13B — Add real simulator gates and publish tested releases

```text
The first complete application slice now exists. Read docs/ci-contract.md and plan.md §11.
Integrate the supplied workflow, Dockerfiles, compose.ci.yml and scripts with the actual backend/frontend.
Implement the documented health/auth/state contracts; do not weaken the tests to hide missing functionality.
Run backend lint/pytest and frontend lint/typecheck/test:ci/build. Build images once, start a uniquely named CI
Compose project using the unchanged official simulator, then run simulator_contract.py, app_smoke.py, and
fault_probe.py. These destructive contract/fault checks may target ONLY the disposable CI simulator.
Add backend tests for concurrent approvals, lost POST responses and restart reconciliation, immutable keys,
DB failure blocking execution, stale data blocking both manual/autopilot writes, and snapshot tick gaps.
On vX.Y.Z tags from main, publish exactly the tested images to GHCR, resolve both digests, and save the release
manifest and evidence. Missing optional ML is allowed; missing required MVP source/tests is a hard failure.
Always retain useful redacted logs before CI cleanup. Do not leak .env, auth tokens, or secrets into artifacts.
Actually run the Docker/official-simulator tests if Docker is available. Otherwise state NOT RUN and provide
the exact next command. Never replace the official simulator with a mock and claim contract verification.
```

## Prompt 13C — Deploy the release to a host and exercise rollback

```text
Read CI_CD_GUIDE.md, deploy/compose.yml, scripts/deploy.sh, scripts/rollback.sh, and docs/ci-contract.md.
Configure the existing authorized deployment host if one is available; do not invent a host or claim deploy
success when none is configured. Keep secrets on the host/in GitHub environment secrets, never committed.
Wire the staging environment, verified SSH known-host entry, GHCR read permission, and smoke-test login.
Deployment must consume the already-tested release manifest, pull both images by digest, take a DB backup,
replace only application services, and verify liveness, readiness, real authenticated frontend→backend reads,
and expected git SHA. It must not rebuild images, reset the simulator, or delete database volumes.
On failure restore the previous backend/frontend manifest and verify it; keep CI red and retain diagnostics.
Add only backward-compatible DB schema changes. Explain why image rollback does not reverse a DB migration.
Rehearse a healthy release and a deliberately failing release on staging, preserve decision history, and save
measured recovery time. If there is no host, finish all local/reviewable configuration and mark remote CD pending.
```

## Prompt 13D — Produce the CI/CD evidence and teach it to us

```text
Review the actual CI runs and deployment, then fill docs/evidence-checklist.md only with real evidence.
Show the chain: commit → tests → official-simulator integration → tested image → digest → deployed SHA.
Rehearse: one failed PR, one successful release, one simulator fault with cached reads and execution blocked,
one healthy recovery, and one application rollback preserving the database and simulator.
Give a 90-second explanation for judges and short answers to: CI vs CD; image vs container vs digest;
liveness vs readiness; why /v1/health can be green during a fault; why healthcheck alone does not restart
an unhealthy container; why lost responses need durable idempotency; and what rollback cannot undo.
Report every unrun check explicitly. Do not invent benchmark, performance, deployment, or recovery numbers.
```

---

## Prompt 14 — Docs, architecture diagram & demo · [MVP] · all

```text
Phase 14. Read plan.md §1, §13, §15, §16, §17. Work in README.md and docs/.

1. README.md: what it is (3 lines), screenshot, architecture diagram, features mapped to the brief's
   requirements (brief §6, §7, §9, §11, §12, §14, §15, §17, §18, §19), quick start, URLs table, demo credentials (the fake ones from
   .env.example), how to run tests / benchmark / load test / chaos drills, limitations and assumptions, team.
2. docs/architecture.md: Mermaid diagram (simulator → backend/ingestion → intelligence → decision → application →
   monitoring, plus ml-service, db, LLM, Prometheus/Grafana), exported to docs/architecture.png
   (npx @mermaid-js/mermaid-cli or its Docker image), one paragraph per component, and "the life of one tick"
   step by step.
3. docs/demo-script.md: the 14 steps from plan.md §13 with exact clicks/commands, expected screen, what to say
   (1–2 sentences each), timing, and a "if this goes wrong" fallback for every risky step.
4. docs/judge-faq.md: 12 likely judge questions with short honest answers (why no RL, how recommendations are
   validated, what happens when the simulator dies, how forecasts are evaluated, what is simulated vs real,
   security, biggest limitation, what we'd do with more time, how it would scale...).
5. Go through plan.md §15: for each deliverable link the evidence (file or URL). List anything missing.
6. Final consistency pass: every URL, port and command in README and docs actually works.

Verify: a teammate who didn't build it follows README from a fresh clone; then a timed dry run with
docs/demo-script.md including the chaos steps.
```

---

## Helper prompts (use any time)

### H1 — Fix an error
```text
This failed. Diagnose before changing code.
What I ran: <COMMAND>
What I expected: <EXPECTED RESULT>
Full output / error:
<PASTE EVERYTHING>
Steps: 1) explain the cause in plain words, 2) propose the smallest fix, 3) apply it, 4) re-run the same command
and show it passes, 5) if it was a logic bug, add a test so it can't come back. Don't touch unrelated files.
```

### H2 — Teach me this part
```text
Explain <FILE OR FEATURE> to me like a beginner who must defend it in front of judges: what problem it solves,
how it works step by step (with a tiny example using our real stations and numbers), what can go wrong, and
3 questions a judge might ask with good answers. No code changes.
```

### H3 — Review before merging
```text
Review this branch against main (git diff main...HEAD) using plan.md and CLAUDE.md: bugs, any way an allocation
could break a limit, missing tests, hardcoded IDs or secrets, missing metrics/logs, anything that could break
`docker compose up`. List issues by severity with file:line and a suggested fix. Don't change code until I say "apply".
```

### H4 — Surprise event from the organizers
```text
The organizers just announced: "<PASTE ANNOUNCEMENT>".
1) Explain what changes for our system (world, API, rules). 2) Check our code for anything that would break
(hardcoded assumptions, schemas, policies, dashboards). 3) Propose the smallest safe change and how we show it in
the demo (detect → evaluate → respond → explain → monitor recovery). Wait for my OK before editing.
```

### H5 — Pre-demo check
```text
Run a pre-demo check without changing code: docker compose ps (all healthy), /api/health all green,
Prometheus targets UP, Grafana dashboards loading, simulator paused at a fresh reset, faults cleared,
autopilot OFF, CHAOS_ENABLED=true, LLM key works or fallback shows, all three demo users can log in.
Print a checklist with ✅/❌ and the exact fix command for every ❌.
```

### H6 — New chat / context refresh
```text
New session. Read CLAUDE.md, plan.md and whatwillbedone.md (check which steps are ticked and their notes).
Summarise in 5 lines where the project stands and what the next step is. Then wait for my prompt.
```


## Integration corrections that apply to every prompt

- Latest API authority is `docs/simulator-guide.pdf` (Final-1); brief examples without `/v1` are conceptual.
- Instance IDs/seeds/ticks alone do not reliably identify every external reset. Persist run_id, process reset notices and your own reset actions, scope history by run_id, and hold execution if generation is uncertain.
- Refresh REST while paused. Coalesce SSE hints; after reconnect do a full refresh. Multiple REST resources are not an atomic snapshot.
- A same-key replay may return 200 or 201 because the guide contradicts itself; accept both and verify the same allocation ID.
- Probe dispatch limits without confusing them with tank capacity or depot inventory: choose fixtures with headroom, distribute test loads, and reset only the isolated test simulator.
- History retrieval gaps must be shown as gaps. Inventory changes do not reveal lost/unmet demand.
- Offline benchmarks may auto-execute policies on a dedicated simulator; consequential live demo decisions still require human review.
- LLM/ML services are optional; a meaningful forecast/detection/heuristic implementation satisfies the intelligence requirement. Do not delay CI waiting for optional services.
