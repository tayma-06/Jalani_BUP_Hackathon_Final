# Evidence checklist — fill with real results only

Status starts **NOT RUN / NOT IMPLEMENTED**. An included configuration file is not operational evidence.

| Evidence | Status | File/link to add |
|---|---|---|
| Local automation validation | See validation-status.md | `docs/validation-status.md` |
| End-to-end working app and source repository | NOT IMPLEMENTED in this pack | Repository URL + screenshot |
| Official simulator integration | NOT RUN | `simulator-contract.json`, image version/digest |
| Demand/risk/valid allocation with explanation | NOT RUN | Recorded decision and measured outcome |
| Human approval + durable idempotency/restart proof | NOT RUN | Backend test report + decision history |
| Red PR followed by repaired green PR | NOT RUN | Two GitHub Actions run links |
| Passing tagged release | NOT RUN | CI run and both image digests |
| Deployed version and SHA | NOT RUN | `deployed.json` + UI/health screenshot |
| Failed candidate and verified rollback | NOT RUN | `rollback.json`, timing, data continuity check |
| Simulator fault and recovery | NOT RUN | `fault-recovery.json` and health screenshot |
| DB failure blocks execution | NOT RUN | Behavioral test + demonstration |
| Monitoring: application/system/intelligence/logs | NOT RUN | Dashboard screenshots + representative redacted logs |
| Meaningful application load test | NOT RUN | `load-dashboard.json`, hardware, CPU/memory, bottleneck analysis |
| Fair policy comparison | NOT RUN | Same seed/events/horizon, results and limitations |
| Architecture and setup | SPECIFIED | `plan.md`, actual README after implementation |
| Final rehearsal | NOT RUN | Actual event time limit, measured run time, backup recording |

For every run, record: date, git SHA, simulator image, configured speed/tick length, scenario/events, machine CPU/RAM, exact command, result, and limitation. Keep passwords, keys, full `.env`, and resolved Compose configuration out of evidence.
