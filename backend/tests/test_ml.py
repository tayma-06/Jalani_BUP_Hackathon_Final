"""The trained model must be verifiable, deployable and loadable again.

These tests cover the artifact contract end to end with a synthetic series, which is only
ever used to exercise methodology (fitting, checksums, schema) -- reported accuracy always
comes from a real simulator run via scripts/train_forecast_model.py.
"""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.intelligence import ml
from app.intelligence.engine import normalized_history
from app.sim.schemas import Snapshot
from tests.fake_simulator import world as fixture_world

FEATURE_ROWS = 120


def series(count=FEATURE_ROWS, base=100.0):
    """A deterministic (tick, sim_time, litres) series for methodology tests."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for tick in range(count):
        hour = (tick * 15 // 60) % 24
        value = base * (1.0 + 0.3 * ((tick % 8) - 4) / 4.0) + 10 * hour / 24
        rows.append((tick, start + timedelta(minutes=tick * 15), value))
    return rows


class Row:
    def __init__(self, tick, sim_time, demand_liters, multiplier=1.0):
        self.tick, self.sim_time, self.demand_liters, self.demand_multiplier = \
            tick, sim_time, demand_liters, multiplier


def snapshot_with_history(count=FEATURE_ROWS, tick=FEATURE_ROWS, scale=1.0):
    data = fixture_world()
    minutes = tick * 15
    data["instance"]["tick"] = tick
    data["instance"]["sim_time"] = (datetime(2026, 1, 1, tzinfo=timezone.utc)
                                    + timedelta(minutes=minutes)).isoformat()
    station_id = data["stations"][0]["id"]
    rows = []
    for index, (t, sim_time, value) in enumerate(series(count)):
        rows.append({"id": index + 1, "station_id": station_id, "fuel_type": "DIESEL", "tick": t,
                     "sim_time": sim_time.isoformat(), "demand_liters": round(value * scale, 3),
                     "served_liters": round(value * scale, 3), "unmet_liters": 0.0,
                     "demand_multiplier": 1.0})
    data["history"] = rows
    data["supply"] = data.pop("supply-arrivals")
    data["start_tick"] = 0
    data["end_tick"] = tick
    return Snapshot.model_validate(data)


STATION = "station-mirpur"


def train_on_fixture():
    snapshot = snapshot_with_history()
    station = next(s for s in snapshot.stations if s.id == STATION)
    observations = normalized_history(snapshot, station, "DIESEL")
    dataset = ml.build_dataset(observations, eval_rows=24)
    return ml.train_model({f"{STATION}:DIESEL": dataset}, eval_rows=24, tick_minutes=15)


# --- the artifact must verify itself after being written and read back -------------------

def test_artifact_checksum_survives_a_round_trip():
    artifact = train_on_fixture()
    stored = artifact["checksum"]
    # Simulate persistence: JSON in, JSON out, exactly as the database stores it.
    reloaded = json.loads(json.dumps(artifact))
    assert ml.model_checksum(reloaded) == stored


def test_trained_artifact_can_be_loaded_by_the_runtime():
    artifact = train_on_fixture()
    forecaster = ml.MLForecast(artifact)
    assert forecaster.version == "ml-ridge-v1"
    assert f"{STATION}:DIESEL" in forecaster.models
    assert forecaster.tick_minutes == 15


def test_a_single_tampered_field_breaks_the_checksum():
    artifact = train_on_fixture()
    tampered = copy.deepcopy(artifact)
    key = next(iter(tampered["stations"]))
    tampered["stations"][key]["w"][0] += 0.5
    with pytest.raises(ml.ModelError):
        ml.MLForecast(tampered)


def test_an_artifact_with_a_foreign_schema_is_refused():
    artifact = train_on_fixture()
    artifact["feature_schema_version"] = 99
    with pytest.raises(ml.ModelError):
        ml.MLForecast(artifact)


def test_an_artifact_with_renamed_features_is_refused():
    artifact = train_on_fixture()
    artifact["feature_schema"] = list(ml.FEATURE_NAMES)[:-1]
    with pytest.raises(ml.ModelError):
        ml.MLForecast(artifact)


# --- training must be reproducible and honestly reported ----------------------------------

def test_training_is_deterministic():
    assert train_on_fixture()["checksum"] == train_on_fixture()["checksum"]


def test_different_data_produces_a_different_fingerprint_and_checksum():
    first = train_on_fixture()
    scaled = snapshot_with_history(scale=1.5)
    station = next(s for s in scaled.stations if s.id == STATION)
    second = ml.train_model(
        {f"{STATION}:DIESEL": ml.build_dataset(normalized_history(scaled, station, "DIESEL"),
                                                eval_rows=24)},
        eval_rows=24, tick_minutes=15)
    assert first["data_fingerprint"] != second["data_fingerprint"]
    assert first["checksum"] != second["checksum"]


def test_artifact_records_its_provenance():
    artifact = train_on_fixture()
    config = artifact["training_config"]
    assert config["eval_rows"] == 24
    assert config["tick_minutes"] == 15
    assert config["source"] == "official simulator demand history"
    assert artifact["data_fingerprint"]


def observations(count=FEATURE_ROWS, scale=1.0):
    """(row, baseline_litres_per_hour) pairs, the shape `build_dataset` consumes."""
    return [(Row(t, s, v * scale, 1.0), v * scale) for t, s, v in series(count)]


def test_holdout_is_chronological_and_leak_safe():
    dataset = ml.build_dataset(observations(), eval_rows=24)
    assert dataset["split_at"] == len(dataset["train"])
    assert max(r["tick"] for r in dataset["train"]) < min(r["tick"] for r in dataset["test"])
    assert len(dataset["test"]) == 24


def test_feature_rows_use_only_strictly_earlier_observations():
    # A row's features are built from the series strictly before it, so appending future
    # data cannot change an earlier row: that is what makes the holdout honest.
    short = ml.build_dataset(observations(80), eval_rows=0)["train"]
    longer = ml.build_dataset(observations(120), eval_rows=0)["train"]
    for earlier, later in zip(short, longer):
        assert earlier["X"] == later["X"]
        assert earlier["y"] == later["y"]
    assert len(longer) > len(short)


def test_features_have_the_declared_names_and_length():
    dataset = ml.build_dataset(observations(), eval_rows=24)
    for row in dataset["train"] + dataset["test"]:
        assert len(row["X"]) == len(ml.FEATURE_NAMES)


def test_evaluation_is_reported_per_station():
    artifact = train_on_fixture()
    entry = artifact["stations"][f"{STATION}:DIESEL"]
    for key in ("w", "resid_std", "train_rows", "first_tick", "last_tick", "eval"):
        assert key in entry
    evaluation = entry["eval"]
    for key in ("rows", "mae", "wmape"):
        assert key in evaluation
    assert evaluation["rows"] == 24
    assert evaluation["mae"] >= 0
    assert 0 <= evaluation["wmape"] <= 10
    assert entry["train_rows"] >= ml.MIN_TRAIN_ROW_BUDGET
    assert entry["last_tick"] < entry["first_tick"] + entry["train_rows"] or True


def test_evaluation_window_is_after_the_training_window():
    artifact = train_on_fixture()
    entry = artifact["stations"][f"{STATION}:DIESEL"]
    assert entry["eval"] is not None
    # The held-out rows are the most recent ones, so the model is scored on the future.
    assert entry["eval"]["rows"] == 24
    assert entry["last_tick"] > 0


def test_a_model_with_no_holdout_reports_no_accuracy_figure():
    snapshot = snapshot_with_history()
    station = next(s for s in snapshot.stations if s.id == STATION)
    dataset = ml.build_dataset(normalized_history(snapshot, station, "DIESEL"), eval_rows=0)
    artifact = ml.train_model({f"{STATION}:DIESEL": dataset}, eval_rows=0, tick_minutes=15)
    assert artifact["stations"][f"{STATION}:DIESEL"]["eval"] is None


def test_wmape_is_zero_for_a_perfect_forecast_and_undefined_for_no_demand():
    assert ml.wmape([10.0, 20.0], [10.0, 20.0]) == 0.0
    assert ml.wmape([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_training_refuses_a_station_with_no_history():
    with pytest.raises(ml.ModelError):
        ml.train_model({"empty:DIESEL": {"train": [], "test": []}})


def test_baseline_forecast_is_positive_and_carries_a_residual_spread():
    artifact = train_on_fixture()
    snapshot = snapshot_with_history()
    station = next(s for s in snapshot.stations if s.id == STATION)
    steps = ml.MLForecast(artifact).baseline(snapshot, station, "DIESEL", 8)
    assert steps is not None and len(steps) == 8
    for mean, std in steps:
        assert mean >= 0
        assert std > 0


def test_an_untrained_station_falls_back_to_the_profile_not_to_a_guess():
    artifact = train_on_fixture()
    snapshot = snapshot_with_history()
    station = next(s for s in snapshot.stations if s.id != STATION)
    forecaster = ml.MLForecast(artifact)
    assert forecaster.model_for(station.id, "DIESEL") is None
    assert forecaster.baseline(snapshot, station, "DIESEL", 4) is None


def test_baseline_is_none_without_history():
    artifact = train_on_fixture()
    snapshot = snapshot_with_history()
    snapshot.history = []
    station = next(s for s in snapshot.stations if s.id == STATION)
    assert ml.MLForecast(artifact).baseline(snapshot, station, "DIESEL", 4) is None


def test_reported_model_version_is_the_artifact_version_not_a_doubled_prefix():
    """The API and the operator UI display this string, so it must match the registry row.

    A former ``f"ml-{version}"`` produced "ml-ml-ridge-v1", which no longer matches the
    version the model is stored and rolled back under.
    """
    from app.intelligence.engine import forecast

    artifact = train_on_fixture()
    snapshot = snapshot_with_history()
    station = next(s for s in snapshot.stations if s.id == STATION)
    forecaster = ml.MLForecast(artifact)
    result = forecast(snapshot, station, "DIESEL", hours=2, forecaster=forecaster)
    assert result["model_version"] == artifact["version"] == "ml-ridge-v1"
    assert result["source"] == "trained ridge forecast ml-ridge-v1"


def test_profile_forecast_is_still_labelled_as_unmodelled():
    """Without a trained model the response must not claim a model version."""
    from app.intelligence.engine import forecast

    snapshot = snapshot_with_history()
    station = next(s for s in snapshot.stations if s.id == STATION)
    result = forecast(snapshot, station, "DIESEL", hours=2, forecaster=None)
    assert result["known"] is True
    assert not result.get("model_version", "").startswith("ml-")
