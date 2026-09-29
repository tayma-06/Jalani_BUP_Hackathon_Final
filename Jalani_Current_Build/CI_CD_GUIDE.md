# CI/CD for Jalani — what to build, how to run it, how to defend it

**Start this work on day one.** Do not leave it until Prompt 13 at the end. The target is a visible chain from a code change to a verified running release, with recovery when deployment fails.

The included files are an integration starter for the proposed FastAPI + React application. They have not run on GitHub or a deployment host here. See `docs/validation-status.md` for the exact validation boundary.

## 1. Understand the words first

| Term | In your project |
|---|---|
| Git repository | Shared history of backend, frontend, tests, infrastructure and documentation |
| CI: continuous integration | Every proposed change gets lint, tests, build and integration checks |
| Image | Packaged application and its runtime dependencies |
| Container | A running instance of an image |
| Registry / GHCR | Where the tested images are stored for the deployment host to pull |
| Release tag | A readable version such as `v0.1.0` |
| Image digest | Content identity such as `sha256:...`; deployments use this exact identity |
| Continuous delivery | A verified release is ready for deployment, possibly with a human release gate |
| Continuous deployment | A successful pipeline updates the configured running environment automatically |
| Rollback | Restore the previous known healthy application release and verify it |

**Publishing an image is not deployment.** Your workflow must actually start that release on a host and verify it. If the deploy job is disabled because no host is configured, describe the result as CI plus automated release packaging.

## 2. What runs when?

| Trigger | Gates | Result |
|---|---|---|
| Pull request | App prerequisites → lint/tests → frontend build → image build → official simulator + app + fault probes | Pass/fail and evidence; no release credentials |
| Push to `main` | Same verification | Verified shared branch |
| Push `vX.Y.Z` tag | Same verification, plus membership in main | Publish tested images, write manifest and deployment bundle |
| Passing tag + configured host + `ENABLE_DEPLOYMENT=true` | SSH transfer → DB backup → pull digests → app update → readiness + real read + SHA check | Running release or verified rollback; failure stays red |

The tag job receives the image archive from the test job. It does **not** rebuild after testing. The manifest binds both service images, version and commit together. Jobs deploy serially to staging, and a host lock prevents simultaneous updates.

## 3. Files supplied

| File | Job |
|---|---|
| `.github/workflows/ci.yml` | Verify, release, optional configured-host deployment |
| `compose.ci.yml` | Separate temporary simulator, database, backend, frontend |
| `docker/*.Dockerfile`, `docker/nginx.conf` | Application packaging and frontend proxy |
| `scripts/ci_preflight.py` | Fail clearly on missing real app files/scripts/locks/tests |
| `scripts/simulator_contract.py` | Reset/step isolated simulator; test allocation/replay/mismatch/cancel |
| `scripts/app_smoke.py` | Read-only login, UI, readiness, freshness and deployed SHA check |
| `scripts/fault_probe.py` | Stale/unavailable fault visibility, cached reads and recovery |
| `scripts/publish_release.py`, `scripts/manifest.py` | Publish tested bytes; enforce immutable release references |
| `scripts/deploy.sh`, `scripts/rollback.sh` | Update or recover a host without resetting its simulator/database |
| `deploy/compose.yml`, `deploy/host.env.example` | Runtime layout and required host configuration |
| `observability/` | Prometheus scrape/alert rules and Grafana starter dashboard |
| `loadtest/dashboard.js` | One meaningful authenticated application load test |
| `tests_ci/` | Local tests of automation logic, including deployment failure control flow |

## 4. Phase 13A: CI before the app is finished

Give `docs/ci-contract.md` to your backend/frontend teammates. They must create the source and locked dependencies that the final workflow expects. The build pack alone deliberately does not pass final CI.

For the first skeleton, create an explicitly named **early CI** check for what exists. Grow it as components arrive. Do not comment out the final release checks and pretend the product passed. Keep meaningful tests for allocation constraints, stale-data execution gates, durable idempotency and permission checks.

Once source is present, the core local commands are:

```bash
python3 -m pip install -r backend/requirements-dev.lock
python3 -m ruff check backend
PYTHONPATH=backend python3 -m pytest backend/tests
python3 -m unittest discover -s tests_ci -v
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test:ci
npm --prefix frontend run build
```

Use a virtual environment locally. Never commit `.env`, passwords, SSH keys or tokens. Configure required PR checks in your repository where supported. Before enabling the pipeline, resolve action tags to verified full commit SHAs; Dependabot can maintain the references. No fabricated action/image hashes are included.

## 5. Phase 13B: run against the official simulator

These commands reset and mutate a **disposable** simulator on ports 18000/18080/13000. Do not substitute a live/demo URL. Do not run another project with the same test ports at the same time.

```bash
export GIT_SHA=$(git rev-parse HEAD)
export APP_VERSION=local-ci
export COMPOSE_PROJECT_NAME=jalani-local-ci
export SMOKE_USER=operator
export SMOKE_PASSWORD=ci-operator
docker compose -f compose.ci.yml config --quiet
docker compose -f compose.ci.yml build backend frontend
docker compose -f compose.ci.yml up -d
python3 scripts/simulator_contract.py --allow-reset
python3 scripts/app_smoke.py --expected-sha "$GIT_SHA"
python3 scripts/fault_probe.py --isolated-ci --expected-sha "$GIT_SHA"
docker compose -f compose.ci.yml logs --no-color > artifacts/local-ci.log
docker compose -f compose.ci.yml down -v
```

The last line removes only this isolated project's data. It is not a production maintenance command. If a test fails, capture diagnostics before cleanup. CI performs cleanup even on failure.

The fault probe is intentionally honest about its coverage. It proves cached reads and visible degradation/recovery for two faults. Backend tests must separately prove that approval/autopilot cannot send writes on stale data or DB failure, and that lost POST responses do not create duplicates.

Record pulled infrastructure image digests on the first successful Docker run. Pin the actual trusted digests in the repository before final rehearsal; do not guess them. The official simulator must remain unchanged.

## 6. Create a release

After all changes are merged and verified on `main`:

```bash
git switch main
git pull --ff-only
git tag v0.1.0
git push origin v0.1.0
```

The tag workflow repeats the gates, publishes to `ghcr.io/<owner>/<repo>-backend` and `...-frontend`, and creates the **release-bundle** Actions artifact. Its `manifest.json` identifies exactly which image digests were published. The workflow's `GITHUB_TOKEN` handles registry publishing with `packages:write`; no personal push token is needed in the YAML.

The repository's Actions/package policies must allow these operations. If publishing is denied, fix the repository/package permissions; do not paste credentials into workflow files. Do not move an already deployed release tag to another commit. Create a new version.

## 7. Phase 13C: choose and prepare the deployment target

Recommended simple target: one Linux amd64 machine with Docker Engine, the Compose plugin, Python 3.11+, Bash, `flock`, SSH, and enough memory/disk for the stack. It can be an existing team/organizer machine; paid cloud is not an organizer requirement. Docker Desktop/WSL is enough for a local demo, but GitHub-hosted runners cannot automatically SSH to your ordinary laptop's localhost.

The first release can be packaged with deployment disabled. Prepare the host once:

1. Create `/srv/jalani`, `incoming/`, and `releases/` owned by the deployment user.
2. Create `/srv/jalani/.env` from `deploy/host.env.example`, replace placeholders, and set mode 600. Match DB credentials and use real application passwords. Do not send this file to GitHub artifacts.
3. Ensure the deploy user can run Docker. Authenticate the host to GHCR with a suitably restricted read credential if images are private. Use Docker's normal login process; do not hard-code the token in a script.
4. Download the **release-bundle** artifact from a passing tag run and transfer `release-bundle.tgz` to the host.
5. Extract the bundle into a temporary directory, generate `release.env`, and move it into its SHA directory:

```bash
mkdir -p /srv/jalani/incoming/first /srv/jalani/releases
tar -xzf release-bundle.tgz -C /srv/jalani/incoming/first
sha=$(python3 /srv/jalani/incoming/first/scripts/manifest.py \
  /srv/jalani/incoming/first/manifest.json \
  --env-output /srv/jalani/incoming/first/release.env)
mv /srv/jalani/incoming/first "/srv/jalani/releases/$sha"
release="/srv/jalani/releases/$sha"
docker compose --project-name jalani --env-file /srv/jalani/.env \
  --env-file "$release/release.env" -f "$release/compose.yml" \
  up -d --wait simulator db
bash "$release/scripts/deploy.sh" /srv/jalani
```

This bootstraps persistent dependencies once. Subsequent releases update backend/frontend with `--no-deps`, which avoids accidentally recreating the simulator. Its undocumented internal data path is not guessed in the Compose file.

The starter binds host interfaces to loopback. View the host's operator UI through SSH forwarding:

```bash
ssh -L 3000:127.0.0.1:3000 your-deploy-user@your-host
```

Then open `http://localhost:3000` on your computer. For a publicly accessible UI, add an HTTPS reverse proxy deliberately; do not publish simulator/admin or database ports.

## 8. Configure GitHub deployment

Create a GitHub environment named **staging**. Environment reviewer/protection features depend on repository visibility and plan; configure only features available to you. A reviewer gate makes this controlled delivery, while an unblocked passing release is automatically deployed.

| Setting | Location | Meaning |
|---|---|---|
| `ENABLE_DEPLOYMENT=true` | **Repository Actions variable** | Enables the deploy job; leave false until the host is ready |
| `DEPLOY_HOST` | Staging environment variable | Hostname or IPv4 address reachable from runner |
| `DEPLOY_USER` | Staging environment variable | Deployment account |
| `DEPLOY_PORT` | Staging environment variable | SSH port; default 22 |
| `DEPLOY_ROOT` | Staging environment variable | Default `/srv/jalani`; no spaces |
| `DEPLOY_SSH_KEY` | Staging environment secret | Private deployment key; not an application password |
| `DEPLOY_KNOWN_HOSTS` | Staging environment secret | Host key entry verified through a trusted channel |

`ENABLE_DEPLOYMENT` must be repository-level because it is used before the environment job starts. Do not disable strict host-key checking to fix a connection error. For a nonstandard SSH port use the corresponding `[host]:port` known-host entry. Application smoke credentials are read privately from the host's resolved Compose environment; they are not printed or uploaded.

Push the next version tag. The deploy job transfers the tested manifest/bundle and runs the host script. Readiness, an authenticated data request through nginx, the expected SHA, baked image revision labels, and running container image IDs must all match. Environment variables alone are not proof that the right code is running. A skipped deploy job is not a successful deployment.

## 9. Rollback and data protection

The deploy script takes a Postgres dump before changing app services, keeps `current` and `previous` release links, and promotes only after verification. A failed candidate attempts to restore the previous backend/frontend pair and checks it. The failed pipeline stays red even when rollback succeeds.

Manual rollback on the host:

```bash
bash /srv/jalani/current/scripts/rollback.sh /srv/jalani
```

Do not roll back blindly across incompatible schema changes. An image rollback does not reverse migrations. Use additive/backward-compatible migrations during the event; do not add destructive automatic migrations to startup. Backups need a separate tested restore procedure and retention policy. No script in the deployment path runs `/admin/reset` or `docker compose down -v`.

The starter has a short service replacement interruption. It is not high availability, blue/green, or zero downtime. The first failed deployment has no previous healthy release to restore; the script reports that condition honestly.

## 10. Monitoring, load testing and judge evidence

Add the `observability/` files to the application's local Compose stack once `/metrics` exists. They provide a starter service-health dashboard; extend it with the intelligence and fuel-network views from Prompt 8. `sim_up` must represent successful real `/v1/*` reads, not `/v1/health`. Prometheus scrape `up` and simulator `sim_up` are different measurements.

Run the included dashboard load test against **your app**, not directly against the simulator:

```bash
mkdir -p artifacts
export BASE_URL=http://localhost:3000
export SMOKE_USER=operator
# Set SMOKE_PASSWORD privately for your local test account.
k6 run loadtest/dashboard.js
```

Record avg, p50, p95, p99, request rate, error rate, active users, and CPU/memory during the run. The script's p95 < 500 ms/error < 1% are team goals, not official marks thresholds. Report an observed failure honestly and explain the bottleneck. Add more load paths only when they exist.

Fill `docs/evidence-checklist.md` with real run links/screenshots/reports. Never present illustrative 72% → 19% risk, guessed stockout hours, or an unrun pipeline as measured results.

## 11. A 90-second explanation for judges

“Each pull request runs our checks and builds the application. We then start a separate instance of the official simulator and test the API contract, the operator read path, and recovery from stale and unavailable responses. A failed check blocks release.

For a passing release tag, we publish the same image bytes we tested. A manifest records backend and frontend digests and the commit SHA. Our configured host pulls those exact images, preserves the database and simulator, and verifies readiness and authenticated network data through the frontend. If verification fails, we restore the previous pair and verify recovery.

Liveness tells us the process is responsive; readiness tells us critical state and storage are usable. Simulator liveness bypasses injected faults, so our health view also measures actual data calls. During a simulator fault we keep showing labeled cached data and block allocation execution. Deployment and failure evidence are retained for the exact release.”

Use only the parts you actually completed. If remote deployment is not configured, say so and demonstrate the reproducible local launch instead.

## 12. Common viva questions

| Question | Answer |
|---|---|
| Why not Kubernetes? | One small simulator and a few services fit Compose; extra orchestration must solve a real problem. |
| Is a green healthcheck enough? | No. We also check readiness, actual authenticated data and the expected release SHA. |
| Why is `/v1/health` green during an outage? | It deliberately bypasses the simulator's fault injection. |
| Does Docker restart every unhealthy container? | No. Healthchecks mark status; restart policies react to process exits. |
| Why use image digests? | They identify the exact tested content even if a readable tag later changes. |
| What if a dispatch response disappears? | Persist the intent/key first, reconcile by the same key, then retry the identical request if needed. |
| What if the database is down? | Keep cached reads but stop execution; we cannot safely persist new intent or audit records. |
| Does rollback restore the database? | No. We require compatible schemas; restoring a DB backup is a separate recovery operation. |
| What proves CI/CD worked? | Linked runs, reports, published digests, running SHA, and a demonstrated rollback. |

Primary references are listed in `docs/source-review.md`.
