"""Train the demand-forecast model from real simulator history, then optionally deploy it.

Reads demand history from a live (or already-stepped) official simulator, fits the pure-NumPy
ridge model in `app.intelligence.ml`, prints the held-out evaluation, and writes the artifact
plus a JSON report. Nothing is invented: with too little history the script fails rather than
reporting a metric it cannot compute.

    # destructive: collects history by stepping an ISOLATED simulator, then trains
    python scripts/train_forecast_model.py --simulator http://127.0.0.1:8000 --collect-ticks 2000 --allow-reset

    # non-destructive: train from whatever history the simulator already has
    python scripts/train_forecast_model.py --simulator http://127.0.0.1:8000

    # also register the artifact with a running backend (requires an admin token)
    python scripts/train_forecast_model.py --simulator ... --base http://127.0.0.1:3000 --deploy

The official simulator binary is never modified; only its own admin endpoints are used.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import urllib.request

from app.intelligence.engine import normalized_history  # noqa: E402
from app.intelligence.ml import (  # noqa: E402
    DEFAULT_EVAL_ROWS,
    ModelError,
    build_dataset,
    deploy_model,
    model_checksum,
    train_model,
)
from app.sim.schemas import FUELS, Snapshot  # noqa: E402

MINIMUM_ROWS = 48  # below this a held-out split is not meaningful


def api(base, path, body=None, method=None, timeout=30):
    """Call the simulator's own REST API and return the parsed JSON."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{base.rstrip('/')}{path}", data=data,
                                     headers={"Content-Type": "application/json"},
                                     method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def fetch_world(simulator):
    """Assemble a full world from the simulator's REST endpoints, discovered by id."""
    instance = api(simulator, "/v1/instance")
    world = {
        "instance": instance, "regions": api(simulator, "/v1/regions"),
        "depots": api(simulator, "/v1/depots"), "stations": api(simulator, "/v1/stations"),
        "routes": api(simulator, "/v1/routes"), "allocations": api(simulator, "/v1/allocations"),
        "events": api(simulator, "/v1/events"),
    }
    for key, path in (("supply", "/v1/supply-arrivals"), ("history", "/v1/demand-history")):
        try:
            world[key] = api(simulator, path)
        except Exception as exc:  # noqa: BLE001 - the report must say what was missing
            world[key] = []
            world.setdefault("collection_warnings", []).append(f"{path}: {exc}")
    world.setdefault("start_tick", 0)
    world["end_tick"] = instance.get("tick", 0)
    try:
        world["metrics"] = api(simulator, "/v1/metrics")
    except Exception:  # noqa: BLE001 - metrics are reporting only, never needed to fit
        world["metrics"] = {}
    return world


def collect_history(simulator, ticks, tick_minutes, allow_reset, log=print, sample_every=20):
    """Step an isolated simulator, sampling its demand history as it goes.

    The simulator keeps only a rolling window of recent history rows, so the rows are
    sampled every `sample_every` ticks and de-duplicated by (station, fuel, tick) rather
    than read once at the end, which would return only the final window.
    """
    if not allow_reset:
        raise SystemExit("--collect-ticks is destructive; pass --allow-reset and use an ISOLATED simulator")
    api(simulator, "/admin/reset", {"confirm": True}, method="POST")
    api(simulator, "/admin/pause", {}, method="POST")
    seen, collected = set(), []
    started = time.monotonic()
    for step in range(1, ticks + 1):
        api(simulator, "/admin/step", {}, method="POST", timeout=60)
        if step % sample_every == 0 or step == ticks:
            for row in api(simulator, "/v1/demand-history"):
                key = (row.get("station_id"), row.get("fuel_type"), row.get("tick"))
                if key not in seen:
                    seen.add(key)
                    collected.append(row)
        if step % 400 == 0:
            rate = step / max(1e-6, time.monotonic() - started)
            log(f"  stepped {step}/{ticks} ticks ({rate:.0f}/s, {len(collected)} history rows)")
    api(simulator, "/admin/pause", {}, method="POST")
    instance = api(simulator, "/v1/instance")
    collected.sort(key=lambda r: (r.get("station_id", ""), r.get("fuel_type", ""), r.get("tick", 0)))
    log(f"  collected {len(collected)} unique demand-history rows up to tick {instance.get('tick')}")
    return collected


def build_groups(snapshot, eval_rows):
    """One leak-safe dataset per station+fuel, using the engine's normalised history."""
    groups, skipped = {}, {}
    for station in snapshot.stations:
        for fuel in FUELS:
            key = f"{station.id}:{fuel}"
            observations = normalized_history(snapshot, station, fuel)
            if not observations:
                skipped[key] = "no usable history (no demand rows, or no event multiplier)"
                continue
            dataset = build_dataset(observations, eval_rows)
            if len(dataset["train"]) + len(dataset["test"]) < MINIMUM_ROWS:
                skipped[key] = f"only {len(dataset['train']) + len(dataset['test'])} rows " \
                               f"(need {MINIMUM_ROWS})"
                continue
            groups[key] = dataset
    return groups, skipped


def summarise(artifact):
    """Aggregate the per-station held-out evaluation into one honest headline."""
    per_station, wmapes = {}, []
    for key, entry in artifact["stations"].items():
        evaluation = entry.get("eval")
        if not evaluation:
            # No held-out window means no accuracy can honestly be claimed for this group.
            per_station[key] = {"mae": None, "wmape": None, "train_rows": entry.get("train_rows"),
                                "test_rows": 0, "evaluated": False}
            continue
        per_station[key] = {"mae": round(evaluation["mae"], 2),
                            "wmape": round(evaluation["wmape"], 4),
                            "train_rows": entry.get("train_rows"),
                            "test_rows": evaluation.get("rows"), "evaluated": True}
        # The artifact stores per-group evaluation counts, not the rows themselves, so a pooled
        # error across groups cannot be recomputed here. Only a mean of the group WMAPEs is
        # available, and it is labelled as such rather than presented as a pooled figure.
        wmapes.append(evaluation["wmape"])
    headline = {"stations_trained": len(artifact["stations"]),
                "station_evaluation": per_station,
                "stations_evaluated": sum(1 for v in per_station.values() if v["evaluated"])}
    if wmapes:
        headline["mean_wmape_across_stations"] = round(sum(wmapes) / len(wmapes), 4)
    return headline


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--simulator", required=True, help="Official simulator URL")
    parser.add_argument("--collect-ticks", type=int, default=0,
                        help="DESTRUCTIVE: step this many ticks to generate history first")
    parser.add_argument("--allow-reset", action="store_true", help="Required with --collect-ticks")
    parser.add_argument("--eval-rows", type=int, default=DEFAULT_EVAL_ROWS,
                        help="Held-out ticks per station+fuel (chronological, leak-safe)")
    parser.add_argument("--output", default="artifacts/forecast-model.json")
    parser.add_argument("--report", default="artifacts/forecast-training-report.json")
    parser.add_argument("--base", help="Backend URL, required with --deploy")
    parser.add_argument("--deploy", action="store_true", help="Register the model with the backend")
    parser.add_argument("--reason", default="scheduled retraining from simulator demand history")
    args = parser.parse_args()

    world = fetch_world(args.simulator)
    if args.collect_ticks:
        print(f"collecting {args.collect_ticks} ticks of real demand history...")
        history = collect_history(args.simulator, args.collect_ticks,
                                  world["instance"].get("tick_minutes", 60), args.allow_reset)
        fresh = fetch_world(args.simulator)
        fresh["history"] = history
        world = fresh

    try:
        snapshot = Snapshot.model_validate(world)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"simulator world is not readable: {exc}") from exc

    groups, skipped = build_groups(snapshot, args.eval_rows)
    if not groups:
        raise SystemExit("no station+fuel has enough history to train; collect more ticks "
                         f"(--collect-ticks). Skipped: {json.dumps(skipped, indent=2)}")
    print(f"training {len(groups)} station+fuel models over "
          f"{sum(len(d['train']) for d in groups.values())} training rows "
          f"({len(skipped)} skipped for insufficient history)")

    tick_minutes = snapshot.instance.tick_minutes
    try:
        artifact = train_model(groups, args.eval_rows, tick_minutes)
    except ModelError as exc:
        raise SystemExit(f"training failed: {exc}") from exc

    if model_checksum(artifact) != artifact["checksum"]:
        raise SystemExit("artifact checksum does not match; refusing to report or deploy")

    report = {"simulator": args.simulator, "seed": snapshot.instance.seed,
              "scenario_id": snapshot.instance.scenario_id,
              "tick_minutes": tick_minutes, "end_tick": snapshot.end_tick,
              "history_rows": len(snapshot.history), "eval_rows": args.eval_rows,
              "model_version": artifact["version"], "checksum": artifact["checksum"],
              "data_fingerprint": artifact["data_fingerprint"],
              "skipped": skipped, "summary": summarise(artifact),
              "deployed": False}
    report.update({k: v for k, v in world.items() if k == "collection_warnings"})

    if args.deploy:
        if not args.base:
            raise SystemExit("--deploy requires --base")
        from http_checks import login, request, require
        admin = login(args.base, os.environ.get("ADMIN_USER", "admin"),
                      os.environ.get("ADMIN_PASSWORD", "demo-admin"))
        from app.db import SessionLocal
        session = SessionLocal()
        try:
            outcome = deploy_model(session, artifact, "system", args.reason)
        finally:
            session.close()
        status, _, data = request(args.base, "/api/models/forecast/deploy", "POST",
                                  {"artifact": artifact, "reason": args.reason}, token=admin)
        require(status == 200, f"backend rejected the artifact: HTTP {status} {data}")
        report["deployed"] = True
        report["deployment"] = outcome

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"\nmodel {artifact['version']} checksum {artifact['checksum'][:16]} "
          f"fingerprint {artifact['data_fingerprint'][:16]}")
    print(f"artifact -> {args.output}\nreport   -> {args.report}")
    if report["skipped"]:
        print(f"note: {len(report['skipped'])} station+fuel groups were skipped for "
              f"insufficient history; see the report for details.")


if __name__ == "__main__":
    main()
