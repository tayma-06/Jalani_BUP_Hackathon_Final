# Source review and corrections — 29 September 2026

## Which source controls what?

| Source | Role |
|---|---|
| `CamScanner 09-29-2026 08.47(2).pdf` (11 scanned pages) | Official challenge, evaluation, deliverables, safety constraints |
| `BUP_Fuel_Supply_Simulator_Integration_Guide_Final-1.pdf` (16 pages) | Latest supplied API/integration guide; copied as `docs/simulator-guide.pdf` |
| `BUP_Fuel_Supply_Simulator_Integration_Guide_Final(3).pdf` (16 pages) | Earlier supplied guide, compared against Final-1 |
| Original `plan.md`, `whatwillbedone.md`, `prompts.md` | Claude's proposed design, not additional organizer requirements |

Both guide PDFs were text-extracted and compared. The scanned brief was rendered, OCR-read, and the evaluation table visually checked. The source PDFs are retained in this pack for checking the original wording.

## What the newest guide actually changes

The only extracted-text difference is in **Hard rules, page 4 / section 2**. The earlier PDF ends a bullet at “are”. Final-1 completes it to explain that crisis events and faults are injected through `/admin/*`, and that participant apps may call these for self-test scenarios.

No endpoint, image version, request field, inventory table, route, tick setting, fault type, or scoring criterion changed between these two supplied integration guides. This is a comparison of their contents; filenames alone were not treated as proof of a major API revision.

## Confirmed requirements

The brief's §19 lists eleven required deliverables: working application, source, official simulator integration, intelligence capability, operator interface, architecture diagram, reproducible deployment, observability evidence, a meaningful failure demonstration, load-test evidence, and final demo.

The score table (§23, page 10) is 20% product/UX; 20% intelligence/decision quality; 15% architecture/integration; 15% DevOps/engineering; 10% resilience; 10% observability/performance; 10% demo/understanding.

CI/CD is strongly encouraged (§12) and listed among recommended deliverables (§20). Reproducible deployment is required. At least **one** meaningful application path must be load-tested (§17); three paths are not mandatory. RL, LLM, a separate ML service, Kubernetes, canary, and blue/green are not required. The brief asks for a meaningful subset of operator views, not every page in Claude's design.

## Corrections to the original plan

| Original weakness | Revised behavior |
|---|---|
| CI ended at image publication | Add an actual configured-host deploy stage, readiness and SHA check, rollback and evidence |
| CI/CD appeared mainly at step 13 | Split into 13A early CI, 13B integration/release, 13C deployment, 13D evidence |
| Healthcheck + restart treated as automatic recovery from any unhealthy state | Restart policies react to exits; health state alone is not a restart mechanism |
| Refresh only on tick changes | Poll while paused too: allocations and faults can change without a new tick |
| Human approval suggested as sufficient for stale data | Allow review but block execution until fresh validation succeeds |
| DB outage buffered writes in memory | Cached reads only; no allocation execution without durable idempotency/audit storage |
| Keys described as sufficient on their own | Persist immutable body/key before sending, lock approval, reconcile unknown outcomes after timeouts/restarts |
| Run reset identified only by decreasing tick | Handle own resets and notices, scope history by durable run ID, hold writes on ambiguous generation |
| Parallel GETs treated as one snapshot | Record start/end ticks and consistency; a REST bundle is not an atomic snapshot |
| Inventory deltas proposed as complete demand fallback | Mark gaps; inventory cannot reveal unmet demand at an empty tank |
| Exact early stockout times implied | Treat them as estimates; measure no-action baseline on the actual image |
| Three load-test workloads and advanced features looked mandatory | Prioritize one real workload and the end-to-end operating loop |
| Benchmark said our policy would win | Report measured comparisons, including losses and finite-supply limits |
| Tag rollback looked sufficient for all state | Preserve data; use compatible DB schemas; image rollback cannot undo a migration |

## Details still requiring a real simulator run

- Dispatch-capacity counting: the guide's “in-flight + pending on this tick” wording is ambiguous.
- Replay HTTP status: §5.4 says 201; §9 says 200. Accept both but require the same allocation ID.
- Demand-history ordering and coverage under a large tick gap.
- Exact region-factor application and how nominal daily demand combines with hourly factors.
- Tick order, pending departure/arrival timing, and arrival overflow behavior.
- Last scheduled supply tick and measured no-action stockout horizon. The guide says 22 arrivals but does not enumerate their full schedule.
- The guide lists `CONSTRAINED` as still shippable without a precise numeric reduction. A 50% cap is a team policy assumption.

Do not fill these with guessed “verified” answers. Use the unchanged official image in an isolated test environment and retain the observations.

## External technical references

Checked against primary documentation for the CI/CD corrections:

- [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/)
- [Compose healthcheck and service behavior](https://docs.docker.com/reference/compose-file/services/)
- [Compose up / wait behavior](https://docs.docker.com/reference/cli/docker/compose/up/)
- [GitHub publishing Docker images](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
- [GitHub deployment environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [GitHub official action repositories](https://github.com/actions) and the official runner workflow for artifact-action versions.

Action majors are explicit in the starter. Before enabling them in the actual repository, resolve trusted tags to full commit SHAs and record them; no unverified hashes are supplied. Lock application dependencies and record base/infrastructure image digests after a successful pull.
