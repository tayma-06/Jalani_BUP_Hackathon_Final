# Troubleshooting

## Sign-in

**"Invalid username or password".** Usernames are the configured names, not email addresses. The local defaults are `operator` / `demo-operator`, `admin` / `demo-admin` and `viewer` / `demo-viewer`, unless your `.env` sets `*_USER` / `*_PASSWORD`. After changing `.env`, run `docker compose up -d backend` to apply it.

**The Control room is missing.** Only `admin` sees it.

## Starting the stack

**`docker: failed to connect to the docker API ... dockerDesktopLinuxEngine`.** Docker Desktop is not running. Start it and wait until `docker info` works.

**A build fails with `CERTIFICATE_VERIFY_FAILED`, `SELF_SIGNED_CERT_IN_CHAIN` or `unable to get local issuer certificate`.** Antivirus HTTPS scanning (for example Avast Web Shield) or a corporate proxy is re-signing TLS traffic, and containers do not trust its root certificate. Either turn off HTTPS scanning while you build, or give the build the extra certificate:

1. Export the intercepting root certificate as PEM. On Windows: `certmgr.msc` → Trusted Root Certification Authorities → the antivirus/proxy root → Export → Base-64 .cer.
2. Build a bundle of the normal public CAs plus that root, e.g. `cat "$(python -c 'import certifi; print(certifi.where())')" avast-root.pem > ca-bundle.pem`. The file is git-ignored.
3. In `.env`, set `EXTRA_CA_FILE=./ca-bundle.pem`, then run `docker compose up -d --build --wait`.

It is used only as a BuildKit secret during `pip wheel` / `npm ci`. It is never copied into an image, and CI does not use it.

**A port is already in use (3000, 8080, 8000, 9090, 9093 or 3001).** Stop whatever uses it, e.g. an earlier `uvicorn` or `npm run dev`, or another stack (`docker ps`).

**An alert fires but nobody is notified.** Open `http://localhost:9093` (Alertmanager). If it shows no receivers, the release is missing `observability/alertmanager.yml` — see the deploy notes. Alerts are also recorded in the app under **Alerts** and **Audit log**, so the incident is still attributable without a notification channel.

**The frontend container is unhealthy but the backend is fine.** `/health` now proxies the backend's liveness, so a frontend health failure means nginx cannot reach the API. Check `docker compose logs backend frontend`; a `JWT_SECRET` shorter than 32 characters is the usual cause.

**The backend stays unhealthy.** Run `docker compose logs backend`. The usual causes are:
- The DB is not ready yet. Compose waits for its health check, so give it a few seconds.
- `DATABASE_URL` does not match `POSTGRES_PASSWORD`.
- A `JWT_SECRET` shorter than 32 characters. The backend refuses to start with one.

## While running

**A yellow banner says "Allocation execution is paused".** This is the safety gate working as intended. The banner text gives the reason:

| Banner reason | Meaning | Fix |
|---|---|---|
| Simulator marked its REST data as stale | A `stale_data` fault is active | Wait for it to expire, or Control room → Clear all faults |
| `CIRCUIT_OPEN` / `TRANSPORT_ERROR` / `FAULT_INJECTED` | Simulator unreachable or faulted | Check `docker compose ps simulator`; clear faults |
| Database unavailable | Postgres is down | `docker compose start db` |
| An uncertain shipment requires ledger reconciliation | A POST response was lost | Wait for the next refresh. If it stays, open Decision history and retry that exact shipment. |
| Simulator moved too far during snapshot reads | The simulator is running very fast | Use `SIMULATION_SPEED=1`, or pause while reviewing |

**Approving says "Simulator tick moved beyond the execution gate".** The simulator advanced more than 8 ticks since the proposal was computed. The page refreshes; review the new proposal and approve again.

**Approving says `ROUTE_DISRUPTED`, `INSUFFICIENT_INVENTORY`, etc.** The world changed after the proposal was made. Nothing was sent. A new proposal will appear if one is still useful.

**No recommendations appear.** This is normal when every station has enough fuel. Run the simulator (Control room → Run) or step it forward; shortages begin after some hours of simulated time. A demand spike event creates risk quickly.

**The dashboard is empty ("Waiting for the fuel network").** The backend has not completed its first valid read. Check System health, then `curl http://127.0.0.1:8000/v1/health`.

**The numbers reset to tick 0.** Someone reset the simulator. The backend starts a new run. Old decision history is kept, but old pending intents are never replayed.

**The briefing says "Grounded template" even though `LLM_API_KEY` is set.** Hover over the label to see the reason. Then check the `llm` component on System health (or `/api/health`):

| Reason | Fix |
|---|---|
| `status: disabled` | The backend didn't get the key. Put `LLM_API_KEY=...` in `.env`, then run `docker compose up -d backend`. |
| `Claude API error 401` | The key is wrong or revoked. |
| `Claude API unreachable or timed out` | No internet access, or antivirus/proxy TLS scanning is blocking `api.anthropic.com`. |
| `Claude used numbers not in the data` | This is the safety check working. Try again on the next tick. |

## Monitoring

**A Grafana panel says "No data".** Counters such as decisions and alerts only get values after the first event of that kind, so approve a shipment or inject an event. Check that Prometheus sees the backend: http://127.0.0.1:9090/targets should show `jalani-backend` as UP.

**Grafana login.** Anonymous viewing is enabled. The admin login is `admin` / `GRAFANA_ADMIN_PASSWORD` (default `demo-grafana`).

## Tests

**`pytest` fails with `PermissionError ... pytest-of-<user>`.** Your temp folder is locked down. Run with `--basetemp=.pytest-tmp`.

**`tests_ci` deployment tests fail on Windows with `WinError 1314`.** They create symlinks, which Windows only allows with Developer Mode or admin rights. Run them in WSL or CI.

**`npm run test:ci` says "No test files found".** You are on an old checkout. The tests live in `frontend/src/App.test.tsx`.

## Resetting everything

```bash
docker compose down        # stop; keeps the database volume
docker compose down -v     # DESTRUCTIVE: also deletes decision history
```
