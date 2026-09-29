# Your output: Jalani build plan and CI/CD integration pack

**What you are building:** a fuel-network control room on top of the organizers' simulator. Your app reads inventories and demand, predicts shortages, recommends valid fuel allocations, lets an operator approve them, and stays useful when dependencies fail.

**What this download contains:** corrected versions of all three planning files, the two authoritative PDFs, actual CI/release/deployment configuration, Docker build templates, simulator/app/fault test scripts, deployment and rollback scripts, monitoring starter files, a load-test script, and an evidence checklist.

**What it does not yet contain:** the FastAPI/React application, a trained model, a live GitHub repository, a running deployment, or measured fuel/performance scores. Those were not supplied. Follow the prompts to build the application. The CI preflight deliberately fails when the required app files are missing. Do not submit this pack alone as a completed hackathon product.

## Read these in this order

1. `docs/source-review.md` — what changed in the last PDF and what needed correction.
2. `plan.md` — the whole product and its constraints.
3. `whatwillbedone.md` — expected outputs, theory, and checks for each step.
4. `CI_CD_GUIDE.md` — your DevOps track, explained from the beginning.
5. `prompts.md` — paste one bounded task at a time into the coding tool in your repository.

## Your immediate actions

1. Unzip into a **new** project folder. If merging into an existing repository, compare files before replacing existing workflows or configuration.
2. On Windows, use your WSL home folder, e.g. `~/jalani`, with Docker Desktop WSL integration enabled.
3. Initialize your Git repository or use your team's existing one. Keep real `.env` files out of Git.
4. Run Prompt 0, then Prompt 1. Use `docs/simulator-guide.pdf` as the API authority; it is the newly supplied `Final-1.pdf`.
5. Run **Prompt 13A immediately** so CI grows with the project. Then implement the first backend/frontend slice, not all optional features at once.
6. Finish the allocation/recovery flow, run 13B, add one meaningful load test, then configure 13C when you have a deployment host.
7. Freeze features, collect evidence, and rehearse. Get the actual judging time limit and any pre-event-code rule from the organizers; neither is established by these files.

## If you are responsible for CI/CD

You own: GitHub Actions; Docker builds; the isolated simulator integration tests; image release; deployment; rollback; logs/metrics; one measured load test; and evidence. You need backend teammates to implement the contracts in `docs/ci-contract.md`.

Start with this command to check the supplied automation itself (Python 3.11+ and Bash):

```bash
python3 -m unittest discover -s tests_ci -v
```

Then read `CI_CD_GUIDE.md`. Docker/real simulator/GitHub/host checks must be run in your actual development environment. `docs/validation-status.md` distinguishes what was checked here from what remains pending.

## Practical minimum product

One operator dashboard; station/fuel forecast and risk; valid allocation recommendations with reasons; manual approval with persistent idempotency; decision history; cached/degraded reads with execution blocked on bad data; health and metrics; a repeatable Docker launch; one fault/recovery demonstration; one load-test report; and the CI/CD release path.

RL, a separate ML service, LLM writing, an LP solver, Kubernetes, and blue/green deployment are optional. Add them only after the core product and evidence work.
