# whatwillbedone.md — what you get after each prompt (+ the theory in plain words)

> Updated 29 September 2026. Checkboxes remain unfilled: none of the application milestones has been executed in this workspace. Read `START_HERE.md` and `CI_CD_GUIDE.md` first.

**How to use this file**
1. Follow the milestone order: 0 → 1 → 13A → 2–8 → 13B → 12 → 13C/13D → 14; add extras afterward. Paste Prompt N from `prompts.md` into your AI agent.
2. Read **"After this prompt"** → you know what to expect before it starts.
3. Read **"Theory"** → you can explain it to judges (they will ask).
4. Run **"Check it works"** → tick the boxes → `git commit` → next prompt.
5. The agent also ticks boxes and adds a short note under each step when it finishes.

Tags: **[MVP]** must have · **[STRONG]** makes us competitive · **[STRETCH]** only if time is left.
Owners: A = backend & integration · B = intelligence · C = frontend · D = DevOps, observability, docs.

---

## The big picture in 60 seconds

The organizers give us a **fake fuel world** (the simulator). It has a clock: every *tick* is 15 simulated minutes. Stations sell fuel every tick, so their tanks go down. Depots hold big stocks and can send trucks (*allocations*) along routes to stations. Ships refill depots on a schedule. The organizers can throw problems at it: demand spikes, broken routes, delayed ships, and even make the simulator's API slow or broken.

The simulator does **not** think. It only answers questions ("how much fuel is at Mirpur?") and obeys one order ("send 5,000 L of diesel from Gazipur to Mirpur on this route").

We build the **brain + control room** on top:
- the **backend** keeps asking the simulator what's happening, stores it, and does the thinking;
- the **intelligence** predicts who will run out and when, and suggests the best truckloads;
- the **frontend** is the screen an operator uses to see everything and approve decisions;
- the **DevOps part** (Docker, CI, monitoring, load tests) proves the system is reliable and measurable.

Judges watch us run the full loop live: **observe → detect → predict → decide → simulate → act → monitor → recover.**

---

## Step 0 — Kickoff · Prompt 0 · [MVP] · all

**After this prompt**
- No app code yet. The agent has read `plan.md` and both PDFs, summarised them back, flagged risks, and created `CLAUDE.md` (its rulebook).

**Theory**
- An AI agent is a very fast junior developer with no memory between chats. `plan.md` + `CLAUDE.md` make every new chat start from the same page.
- You are the tech lead: give one clear task at a time, check the result, commit. Never accept code you haven't run.

**Check it works**
- [ ] The agent's summary matches plan.md §0 (fix misunderstandings now — they get expensive later)
- [ ] `CLAUDE.md` exists in the repo root
- [ ] Everyone on the team can explain the 8-step loop in their own words

Notes:

---

## Step 1 — Repo skeleton + simulator + exploration · Prompt 1 · [MVP] · A

**After this prompt**
- Folder structure, `.gitignore`, `.gitattributes`, `.env.example`, `Makefile`, README quick start.
- `docker-compose.yml` running the **simulator** and **Postgres**.
- `scripts/explore.py` prints the whole world as tables (stations, depots, routes, supply schedule, hours of cover, supply vs demand) and saves `data/world_snapshot.json`.
- `docs/assumptions.md` with real answers to the "must verify" questions in plan.md §4.

**Theory**
- **Docker image** = a packaged app. **Container** = a running copy of it. **docker compose** = a recipe that starts many containers together with one command. Everyone gets identical versions → no "works on my machine".
- **REST API** = talking to a server over HTTP: `GET` to read, `POST` to act. Swagger (`/docs`) lets you try endpoints in the browser.
- **Ticks & speed:** the sim clock jumps 15 minutes per tick; `SIMULATION_SPEED` = ticks per real second. At the default 8, a whole sim day passes in 12 s — so we use 1 while building.
- **Why explore first:** you can't design decisions without knowing the numbers — how fast stations drain, how much supply exists, what the limits really do.

**Check it works**
- [ ] `docker compose up -d simulator db` → `curl localhost:8000/v1/health` says ok
- [ ] `localhost:8000/admin` → press Run → tick increases → Pause
- [ ] `python scripts/explore.py` prints tables; `data/world_snapshot.json` exists
- [ ] `docs/assumptions.md` answers: dispatch-capacity rule, region factor, demand-history order, supply vs demand, **last supply tick** (after it no new fuel arrives), 200 vs 201

Notes:

---

## Step 2 — Backend foundation · Prompt 2 · [MVP] · A

**After this prompt**
- A FastAPI **backend** container on :8080 that refreshes simulator state every second, including while paused, validates it, caches it, stores history in Postgres.
- Endpoints `/api/health`, `/api/network/state`, `/api/supply`, `/api/events`, `/metrics`, Swagger at `/docs`.
- A robust simulator client: timeouts, retries with backoff, error parsing, stale-data flag. Tests for all of it.

**Theory**
- **Why a backend in the middle:** one place for logic and caching; the UI never talks to the simulator directly; we protect the simulator from load; we can survive when it fails.
- **Polling:** ask every second "anything new?". Simple and reliable (push via SSE comes in Step 7).
- **Validation:** never trust incoming data. Pydantic checks the shape; we also check sanity (no negative stock).
- **Timeouts & retries:** a request that hangs forever freezes your app. A timeout gives up; a retry tries again after growing waits (≈1 s, 2 s, 4 s + a little randomness called *jitter*) so everyone doesn't retry at the same instant.
- **Database:** memory is wiped on restart; Postgres keeps history (needed for forecasting and the audit trail).

**Check it works**
- [ ] `localhost:8080/docs` opens
- [ ] Run the sim → call `GET /api/network/state` twice → inventories change
- [ ] `GET /api/health` lists backend, database, simulator
- [ ] `pytest` passes (retry on 503, no retry on 409, stale header, reset detection)

Notes:

---

## Step 3 — Frontend MVP · Prompt 3 · [MVP] · C

**After this prompt**
- React operator console on :3000 (nginx container): layout with left nav, top bar (sim clock, mode chip, "SIMULATED" badge), **Overview** (KPI cards, network schematic, top risks) and **Stations & Depots** (inventory bars).
- Mock mode so the UI can be built before the backend is finished.

**Theory**
- **Components** = Lego blocks of UI. **TanStack Query** fetches data, refetches every 2 s, and keeps the last good data if the backend dies — free resilience.
- **nginx** serves the built website and forwards `/api` to the backend → one address, no CORS errors.
- **Operator UX:** the important things big and coloured (green / amber / red), details one click away. An operator should understand the network in 5 seconds.

**Check it works**
- [ ] `localhost:3000` shows live bars moving while the sim runs
- [ ] Stop the backend container → UI keeps the last data and shows "Offline since …"
- [ ] Network schematic is drawn from API data (no hardcoded station names)

Notes:

---

## Step 4 — Intelligence v1a: forecast, risk, detection, alerts · Prompt 4 · [MVP] · B

**After this prompt**
- **Forecaster:** learns each station's demand pattern by hour, scales by the current multiplier, self-corrects.
- **Risk engine:** "hours until empty" + **stockout probability** for every station/fuel.
- **Detectors + alerts:** demand anomaly, supply delay/shortfall, route/station/depot status, bottleneck, regional risk.
- UI: hours-to-stockout + risk badges, Top risks, regional demand panel, **Alerts** page, **Forecasts** chart.

**Theory**
- **Forecasting:** demand repeats daily with busy hours. Average what happened at each hour (the *profile*), multiply by today's demand multiplier, and nudge it with a correction factor when reality drifts.
- **Inventory position** = fuel in the tank + fuel already on a truck heading there. Ignore trucks → you double-order. Ignore timing → you overflow the tank.
- **Projection:** step forward 15 min at a time: subtract forecast demand, add arriving trucks. The first time it hits 0 = the stockout ETA.
- **Monte Carlo:** demand is noisy, so simulate 300 slightly different futures. If 216 of 300 run dry → risk 72%. Same idea as "72% chance of rain".
- **z-score:** z = (actual − expected) ÷ typical error. |z| > 3 means "very unusual" → alert.

**Check it works**
- [ ] Run ~5 sim hours at speed 1 → Tongi DIESEL hours-to-stockout falls, risk rises
- [ ] Inject `demand_spike` (region-dhaka, ×1.8) → anomaly alert within a few ticks, tagged "confirmed by event"
- [ ] Tests pass (projection with hand-calculated numbers; risk ≈ 0 with a full tank, ≈ 1 with an empty one)

Notes:

---

## Step 5 — Intelligence v1b: recommender, what-if, confidence, explanations · Prompt 5 · [MVP] · B

**After this prompt**
- **Greedy recommender** that proposes depot + route + litres, never breaking a limit, splitting big loads, protecting each depot's home region, switching to cross-region routes when needed.
- **What-if:** risk before → after, plus alternatives (half load, other depot, do nothing).
- **Confidence score** and **template explanations** ("why" + "what limited the quantity").
- `GET /api/recommendations`, `POST /api/decide?dry_run=true`, `backend/config/policy.yaml`.

**Theory**
- **Greedy = triage:** handle the most urgent case first with the best option available, then the next. Not always perfect, but fast, predictable and easy to explain.
- **Constraints** are the rules of the world: truck size (`max_shipment`), depot stock, depot dispatch per tick, station tank space, route status, station status. Break one and the simulator rejects the order (409).
- **Depot reserve:** don't send fuel to another region if your own region will need it before the next ship arrives.
- **What-if** is the "Simulate" step: rerun the same 300 futures with the truck added. The drop in risk is the value of the decision.
- **Confidence:** how much to trust a recommendation (forecast accuracy, fresh data, calm conditions). Low → a human decides.

**Check it works**
- [ ] `GET /api/recommendations` shows cards where risk_after < risk_before
- [ ] Tests prove no recommendation ever breaks a limit (random small worlds)
- [ ] Inject `route_disruption` on route-gazipur-mirpur → Mirpur recommendations switch to route-patiya-mirpur

Notes:

---

## Step 6 — Decision workflow · Prompt 6 · [MVP] · A + C

**After this prompt**
- **Recommendations page** with cards like the brief's example → Approve / Modify / Reject.
- **Executor** sends allocations with idempotency keys, handles every error code, tracks PENDING → IN_TRANSIT → ARRIVED.
- **Decision History** page (who decided, what happened, outcome). **Autopilot** switch with safety gates. **Login** with viewer / operator / admin roles. Audit log.

**Theory**
- **Human-in-the-loop:** the system proposes, the human decides. Autopilot only for confident, routine moves.
- **Idempotency:** persist each order body and unique key before sending it, then reconcile uncertain outcomes after timeouts or restarts. If the network glitches and we resend, the simulator recognises the key and doesn't send a second truck — like an order number.
- **Error codes are instructions:** each 409 tells you what to do next (wait a tick, reroute, shrink the load).
- **Audit trail:** who decided what, when, why — needed for trust and for judges.
- **JWT:** after login the server gives a signed token; every request carries it; the role inside decides what you may do.

**Check it works**
- [ ] Approve a card → it shows in `localhost:8000/v1/allocations` → ARRIVED ~3 ticks later → station bar jumps
- [ ] Double-click Approve → still exactly one allocation
- [ ] Viewer account cannot approve
- [ ] Autopilot ON → HIGH-confidence recs execute automatically, LOW ones wait for review

Notes:

---

## Step 7 — Live stream + resilience + Control Room · Prompt 7 · [MVP → STRONG] · A

**After this prompt**
- **SSE listener** (fast change hints) with watchdog and polling fallback.
- **Circuit breaker**, **last-known-good cache**, **degraded mode**, stale-data handling, invalid-data guard, reset detection.
- UI **banners** (red / yellow / blue) and a **Control Room** page to run/pause/step the sim and inject events, faults and chaos.
- `docs/runbook.md`: what to do for each failure.

**Theory**
- **SSE (Server-Sent Events):** the server keeps a connection open and pushes "something changed". Fast, but messages can be lost → we always re-read the truth via REST.
- **Circuit breaker** = the fuse in your house. After repeated failures it "trips" and stops calling the broken service for a while, then tests it carefully (*half-open*) before going back to normal. Your app stops waiting on a dead service.
- **Graceful degradation:** when something breaks, keep doing what you still can (show last data, read-only) and be honest about it (banner with data age).
- **Never execute on stale data:** manual approvals and autopilot are blocked. Humans can inspect or queue a proposal until fresh validation succeeds.
- **Admin bypass:** the simulator's `/admin/*` and `/v1/health` ignore injected faults. Good: the Control Room keeps working during an outage. Trap: `/v1/health` can say "ok" while every real `/v1/*` call fails — so we judge simulator health by real data calls, never by `/v1/health` alone.

**Check it works**
- [ ] Fault `unavailable` 60 s → red banner, last data shown, autopilot paused → recovers by itself after
- [ ] Fault `stream_disconnect` → "polling" chip, UI still updates
- [ ] Fault `stale_data` → yellow banner; review allowed, manual/autopilot execution blocked
- [ ] Chaos crash → backend restarts automatically, UI reconnects
- [ ] Chaos corrupt-next → "Invalid simulator data" alert, previous data kept

Notes:

---

## Step 8 — Observability · Prompt 8 · [MVP] · D

**After this prompt**
- All metrics from plan.md §8.1, **Prometheus** (:9090) and **Grafana** (:3001) with 3 dashboards, alert rules, JSON logs.
- **System Health** page like the brief's example (components, p95, error rate, version).

**Theory**
- **Metrics** = numbers over time (how many, how fast, how many failed). **Logs** = a diary of events. **Traces** = one request's journey (optional).
- **RED method:** every service shows **R**ate, **E**rrors, **D**uration.
- **Percentiles:** p95 = 95% of requests are faster than this. Averages hide the slow requests that annoy users.
- **Prometheus** pulls (*scrapes*) metrics every few seconds; **Grafana** draws them; **alert rules** fire when a condition holds for a while.

**Check it works**
- [ ] `localhost:9090/targets` → all UP
- [ ] Grafana dashboards show live data
- [ ] Latency fault (800 ms) → p95 panel jumps
- [ ] `unavailable` fault → `SimulatorDown` firing at `localhost:9090/alerts`
- [ ] Dashboard screenshots saved in `docs/observability/`

Notes:

---

## Step 9 — ML service · Prompt 9 · [STRONG] · B

**After this prompt**
- `scripts/generate_history.py` builds a training dataset from the simulator (documented as generated data).
- `train.py` trains a gradient-boosting model, compares it with the baseline (MAE/MAPE), saves versioned models + `experiments.csv` + `docs/model-report.md`.
- **ml-service** container (:8001) with `/predict`, `/model`, `/model/activate`; backend uses it with timeout + fallback; Forecasts page shows model badge and live error.

**Theory**
- **Supervised learning:** show the model many examples of (inputs → answer) so it learns the pattern. Inputs: station, fuel, hour, multiplier, recent demand. Answer: next tick's demand.
- **Time-based split:** train on the past, test on the later part. Random splits leak the future and look falsely good.
- **MAE** = average mistake in litres; **MAPE** = the same as a percentage. Always compare with a simple baseline — if ML doesn't beat it, say so honestly (judges respect that).
- **Separate service:** if the model crashes, only forecasting degrades and the app falls back. **Versioning** lets you roll back a bad model.

**Check it works**
- [ ] `docs/model-report.md` shows baseline MAE vs model MAE
- [ ] `docker compose stop ml-service` → "Fallback forecaster" badge, recommendations continue
- [ ] Start it again → ML badge back
- [ ] Activate the previous model version → `/model` shows it (model rollback)

Notes:

---

## Step 10 — Benchmark (+ LP optimizer) · Prompt 10 · [STRONG] (LP = [STRETCH]) · B

**After this prompt**
- `scenarios/*.yaml` (calm, demand spike, route disruption + delay, combined crisis).
- Benchmark CLI replays each scenario with each policy (do-nothing, naive rule, ours) and writes `docs/benchmark-results.md` with a table + chart.
- Optional LP optimizer + policy switch in the UI (policy rollback).

**Theory**
- **Determinism:** same seed + same events + same actions = same result. So any score difference comes only from the policy → a fair experiment.
- **Baselines:** "good" only means something compared with something. Do-nothing shows how big the problem is; the naive rule shows what a simple approach gets.
- **Linear programming:** write the goal (minimise weighted shortage + transport cost) and the limits as equations; a solver finds the best combination all at once instead of one-by-one like greedy.

**Check it works**
- [ ] Table shows service level per scenario × policy
- [ ] Compare all policies on the same scenarios, seed, horizon, and information. Report wins and losses; tune on development scenarios, then use separate evaluation scenarios.

Notes:

---

## Step 11 — GenAI · Prompt 11 · [STRONG] · A or C

**After this prompt**
- **Incident briefs** when a crisis is detected, a **Summarize network** button, plain-language explanations in the "Why?" panel.
- Works with any OpenAI-compatible API key; falls back to templates without one.

**Theory**
- **LLM as a writer, not a decision maker:** numbers and decisions come from our engine; the LLM only turns facts into readable text.
- **Grounding:** give it only the facts it may use and tell it not to invent anything → far fewer hallucinations.
- **Fallback:** no key, timeout or error → template text. The app never depends on the LLM.

**Check it works**
- [ ] Inject the combined crisis → an incident brief appears, labelled "AI-generated"
- [ ] Blank `LLM_API_KEY` → template text appears, nothing breaks

Notes:

---

## Step 12 — Load testing · Prompt 12 · [MVP] · D

**After this prompt**
- k6 scripts for the implemented workloads (at least one meaningful path is required; three are optional) (dashboard read, decision dry-run, prediction), runnable via `make loadtest`.
- `docs/load-test-results.md` with avg / p50 / p95 / p99, req/s, error %, VUs, CPU/RAM, the bottleneck, one fix with before/after.

**Theory**
- **Load test** = many fake users at once. You learn how fast (latency), how much (throughput) and when it breaks (errors).
- **VUs (virtual users)** = concurrency. Ramping them up shows the breaking point.
- **Caching:** compute once per tick, serve the same answer to everyone → reads stay fast under load. The simulator is never hammered.

**Check it works**
- [ ] Results file has real numbers for every implemented workload
- [ ] Before/after row for the fix
- [ ] Exact reproduce command written in the doc

Notes:

---

## Step 13A — Early CI · after Step 1 · D

**After this prompt:** every PR runs real lint/tests/build for existing components. Missing application code is called out as pending; nothing is silently reported complete. The final release gate requires every MVP component.

**Theory:** CI means checking a proposed code change automatically before it joins the shared branch. A workflow YAML is the recipe; a green run is evidence the recipe actually worked for that commit.

**Check it works**
- [ ] Commit a deliberately failing test on a disposable branch; CI is red.
- [ ] Fix it; CI is green. Save both run links.
- [ ] PR jobs have no SSH/production secrets. Dependencies are locked.

## Step 13B — Integration and immutable releases · after the first complete app slice · D

**After this prompt:** tests boot the unchanged official simulator in a separate CI project. The suite checks real contracts, same-key replay, application login/data, stale/unavailable faults and recovery. A release tag publishes exactly the tested backend/frontend images and writes their digests to one manifest.

**Theory:** unit tests check pieces; integration tests check pieces talking together. A tag is a human-readable name; a digest identifies the exact image content. Rebuilding after testing can produce different bytes, so publish the tested bytes.

**Check it works**
- [ ] `scripts/simulator_contract.py` passes against the official image.
- [ ] `scripts/app_smoke.py` passes through the frontend proxy, with authentication.
- [ ] `scripts/fault_probe.py` sees DEGRADED, cached reads, and recovery on real injected faults.
- [ ] A failed gate prevents publication. A passing tag produces both image digests and reports.

## Step 13C — Actual deployment and rollback · once a host is configured · D

**After this prompt:** a successful tagged release reaches a configured Linux host, pulls tested images by digest, replaces only application services, and verifies readiness and git SHA. On failure it restores the previous manifest and verifies recovery. The database and simulator remain intact.

**Theory:** publishing means putting a package on a shelf; deployment means running that package on the target computer. Rollback means restoring a known good release. Database changes are separate: an old container cannot magically undo a destructive schema migration.

**Check it works**
- [ ] Host, SSH known-host entry, GHCR access, and `.env` are configured.
- [ ] A release tag causes the configured deploy job to run; the UI and health endpoint show that SHA.
- [ ] Deploy a failing candidate on staging; rollback restores both prior images and CI stays red.
- [ ] Decision history is preserved; no simulator reset occurred.
- [ ] If no reachable host is available, record automated deployment as **not configured**. A local Compose launch is reproducible deployment, not proof of remote CD.

## Step 13D — Evidence and viva rehearsal · before Step 14 · all

**After this prompt:** `docs/evidence-checklist.md` links to actual runs, reports, image digests, deployment and rollback. No invented timings, scores, or screenshots.

**Theory:** judges need to see the chain from commit to running app, and what happens when that chain fails. Liveness, readiness, and overall component health answer different questions.

**Check it works**
- [ ] Explain CI vs release vs deployment in your own words.
- [ ] Show one rejected change, one healthy release, one dependency failure/recovery, and one rollback.
- [ ] Explain why `/v1/health` still returns 200 during a simulator outage.
- [ ] Explain why healthchecks alone do not restart unhealthy Docker containers.
- [ ] Rehearse the final story within the organizers' actual time limit.

Notes: The pack includes starter automation and local validation results. External GitHub, Docker, simulator, and host checks remain pending until run in the application repository.

---

## Step 14 — Docs & demo · Prompt 14 · [MVP] · all

**After this prompt**
- README (setup, URLs, features mapped to the brief), `docs/architecture.md` (Mermaid diagram + PNG), runbook, assumptions, data doc, `docs/demo-script.md`, `docs/judge-faq.md`, deliverables checklist with links to evidence.

**Theory**
- Judges score architecture (15%) and understanding (10%): a clear diagram, honest assumptions and a smooth story earn those points.
- The brief ends with "keep it working" → rehearse the failures, not just the happy path.

**Check it works**
- [ ] A teammate who didn't build it runs everything from README alone
- [ ] Demo fits the time slot twice in a row, including the chaos steps
- [ ] Every item in plan.md §15 links to evidence

Notes:

---

## Glossary

| Term | Meaning |
|---|---|
| Tick | one step of the sim clock = 15 simulated minutes (96 per sim day) |
| Allocation | an order to move fuel from a depot to a station along a route |
| Idempotency key | unique order id so a retried request can't create a duplicate |
| REST / SSE | ask-and-answer HTTP API / server pushes messages over an open connection |
| Image / container / compose | packaged app / running copy / recipe to run many together |
| Healthcheck | a command Docker runs to see if a container is alive and ready |
| Circuit breaker | stops calling a failing service for a while, then tests it carefully |
| Backoff + jitter | wait longer after each failure, plus randomness so clients don't retry in sync |
| Degraded mode | running with reduced features but still useful and honest about it |
| Inventory position | on-hand fuel + fuel already on the way |
| Monte Carlo | simulate many random futures and count outcomes to get a probability |
| z-score | how many "typical errors" away from normal a value is |
| MAE / MAPE | average forecast error in litres / in percent |
| p50 / p95 / p99 | 50% / 95% / 99% of requests are faster than this |
| Throughput | requests handled per second |
| VU | virtual user in a load test (concurrency) |
| CI / delivery / deployment | automatic verification / a tested release ready to ship / updating a running environment |
| JWT | signed login token that carries your role |
| LP | linear programming: a solver finds the best plan under limits |
| Service level | served ÷ (served + unmet) — the simulator's main score |
