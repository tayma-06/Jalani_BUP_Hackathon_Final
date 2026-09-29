# Validation status — 29 September 2026

## Passed in this workspace

- 14 local automation tests passed. These cover manifest validation, mutable-tag/newline rejection, baked image revision checks, constrained test-shipment selection, error envelopes, stale/wrong-SHA smoke-check rejection, successful release promotion, and failed candidate rollback preserving the current release while returning failure.
- Deployment tests execute the actual Bash deployment control flow against a **fake Docker command** and a test smoke helper. They do not run Docker containers or prove live rollback.
- All Python files parse. YAML and JSON files parse. All shell scripts and workflow shell steps pass `bash -n`. The k6 script passes JavaScript syntax checking.
- Workflow dependency checks confirm release depends on verification, deployment depends on release, and CI cleanup runs with `always()`.
- The application preflight correctly fails on this pack because actual backend/frontend source and dependency locks are absent. It does not silently pass an empty project.
- Markdown code fences are balanced. Both supplied simulator guides were text-compared; the evaluation and DevOps sections of the scanned brief were visually checked.

## Not run / not available

| Check | Why it is pending |
|---|---|
| Docker build and Compose schema/runtime validation | Docker is not installed in this workspace. YAML parsing is not Compose validation. |
| Official simulator contract / faults | Requires running the published image; no substitute simulator was presented as official evidence. |
| Backend/frontend unit tests or working UI | No application source repository was supplied or implemented by this documentation/CI-CD pack. |
| GitHub Actions execution / GHCR publishing | No project repository was supplied or connected for this task. |
| SSH deployment, real backup/rollback and data continuity | No deployment host or credentials were supplied. |
| Prometheus rule execution / Grafana panels | Configuration only; runtime and metric emitters need the application. |
| k6 performance measurements | Syntax checked only; no running application target. |
| Forecast calibration / policy benchmark / observed stockout times | Require the implemented intelligence and actual simulator runs. |

No deployment success, performance numbers, service-level improvements, or live recovery times are claimed. The README/plan checkboxes remain incomplete until supported by actual evidence.

The raw local test output is in `docs/local-validation.txt`. See `CI_CD_GUIDE.md` for the commands needed to complete the pending checks.
