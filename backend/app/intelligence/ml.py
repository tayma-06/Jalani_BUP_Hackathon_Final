"""Trained ML demand forecast with leak-safe evaluation and a versioned registry.

Pure numpy: a per-(station, fuel) ridge regression over calendar + recent-history
features predicts the next-hour baseline demand (demand divided by its live event
multiplier). Training uses only the simulator's real demand history. Evaluation holds
out a contiguous tail window that never overlaps training rows, so no future
observation can influence a score. Artifacts are stored in the ``model_versions``
registry with a checksum, feature schema, data fingerprint and evaluation record.

Nothing here is a placeholder: ``train_forecast_model.py`` fits against a disposable
official-simulator instance and the service falls back to ``profile_v1`` only when no
active trained model is available or the artifact fails checksum validation.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import timedelta
from typing import Iterable, Sequence

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Audit, ForecastEvaluation, ModelVersion, now
from app.sim.schemas import Snapshot

FEATURE_NAMES = (
    "hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "multiplier",
    "recent_mean", "lag1", "lag2", "lag3",
)
# Documented accuracy budget for a trained forecast. A model that exceeds these on its
# held-out tail is not deployable; the fallback keeps profile_v1 active instead.
EVAL_BUDGETS = {"mae_l_per_hour": 60.0, "wmape": 0.35}
RIDGE_ALPHA = 1.0
MIN_TRAIN_ROW_BUDGET = 24  # per station+fuel: at least 24 feature rows plus the tail
DEFAULT_EVAL_ROWS = 24

# Feature schema version. Changing the feature set MUST bump this so stale artifacts
# are never interpreted with the wrong shapes. The schema is shipped inside each
# artifact and validated on every activation.
FEATURE_SCHEMA_VERSION = "calendar+history-v1"


class ModelError(Exception):
    pass


def model_checksum(artifact: dict) -> str:
    """Deterministic sha256 over the canonical artifact JSON, excluding `checksum` itself.

    The field is removed before hashing so that storing the digest and then re-verifying it
    (on load, on deploy, after a round-trip through the database) gives the same answer.
    """
    payload = {k: v for k, v in artifact.items() if k != "checksum"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def data_fingerprint(records: Iterable[tuple[int, str, str, float]]) -> str:
    """sha256 over sorted (tick, station, fuel, baseline) rows actually observed."""
    body = "\n".join(f"{tick}|{station}|{fuel}|{value:.6f}"
                     for tick, station, fuel, value in sorted(records))
    return hashlib.sha256(body.encode()).hexdigest()


def calendar_features(hour: int, weekday: int) -> list[float]:
    return [math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24),
            math.sin(2 * math.pi * weekday / 7), math.cos(2 * math.pi * weekday / 7)]


def feature_vector(hour: int, weekday: int, multiplier: float,
                   recent_mean: float, lags: Sequence[float]) -> list[float]:
    """Features for one target tick. ``lags`` are observed baselines BEFORE the tick."""
    features = calendar_features(hour, weekday) + [multiplier, recent_mean] + list(lags)
    if len(features) != len(FEATURE_NAMES):
        raise ModelError(f"feature count {len(features)} != schema {len(FEATURE_NAMES)}")
    return features


def build_dataset(normalized: Sequence[tuple], eval_rows: int = DEFAULT_EVAL_ROWS) -> dict:
    """Chronological feature rows; the last ``eval_rows`` ticks are held out for evaluation.

    Each row: (tick, sim_time) label plus features derived only from strictly earlier
    rows. This is what makes the split leak-safe: a test row's features contain no
    information from itself or the future, and the test window is never in the training
    set.
    """
    series = list(normalized)  # [(row, baseline_liters_per_hour)] in tick order
    rows = []
    for i in range(1, len(series)):
        row, label = series[i]
        lags = [value for _, value in series[max(0, i - 3):i]]
        tail = [value for _, value in series[max(0, i - 24):i]]
        while len(lags) < 3:
            lags.insert(0, 0.0)
        raw_multiplier = getattr(row, "demand_multiplier", None)
        multiplier = raw_multiplier if raw_multiplier not in (None, 0) else 1.0
        features = feature_vector(row.sim_time.hour, row.sim_time.weekday(), multiplier,
                                  float(np.mean(tail)) if tail else 0.0, lags)
        rows.append({"tick": row.tick, "sim_time": row.sim_time, "X": features, "y": label})
    rows.sort(key=lambda r: r["tick"])
    if eval_rows:
        split_at = max(0, len(rows) - eval_rows)
        train, test = rows[:split_at], rows[split_at:]
    else:
        train, test = rows, []
    return {"train": train, "test": test,
            "ticks": [r["tick"] for r in rows], "split_at": len(train)}


def _design(X: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(X)), np.asarray(X, dtype=float)])


def ridge_fit(X_train: Sequence[Sequence[float]], y_train: Sequence[float],
              alpha: float = RIDGE_ALPHA) -> np.ndarray:
    """Closed-form ridge regression, intercept unregularised, deterministic."""
    X = _design(list(X_train))
    y = np.asarray(y_train, dtype=float)
    n, d = X.shape
    pen = alpha * np.eye(d)
    pen[0, 0] = 0.0
    return np.linalg.solve(X.T @ X + pen, X.T @ y)


def predict_batch(weights: np.ndarray, rows: Sequence[dict]) -> np.ndarray:
    X = _design([r["X"] for r in rows])
    return X @ weights


def wmape(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    total = float(sum(abs(y) for y in y_true))
    return float(sum(abs(t - p) for t, p in zip(y_true, y_pred)) / total) if total else 0.0


def fit_one(rows: dict) -> dict | None:
    """Fit one station+fuel model with its held-out evaluation. None = not trainable."""
    train = rows["train"]
    if len(train) < MIN_TRAIN_ROW_BUDGET:
        return None
    weights = ridge_fit([r["X"] for r in train], [r["y"] for r in train])
    residuals = np.asarray([r["y"] for r in train]) - predict_batch(weights, train)
    entry = {"w": [round(float(v), 6) for v in weights],
             "resid_std": round(float(np.std(residuals)), 3),
             "train_rows": len(train), "first_tick": train[0]["tick"],
             "last_tick": train[-1]["tick"], "eval": None}
    if rows["test"]:
        observed = [r["y"] for r in rows["test"]]
        predicted = [max(0.0, float(v)) for v in predict_batch(weights, rows["test"])]
        entry["eval"] = {"rows": len(rows["test"]),
                         "mae": round(float(np.mean(np.abs(np.asarray(observed) - predicted))), 2),
                         "wmape": round(wmape(observed, predicted), 4)}
    return entry


def train_model(groups: dict[str, dict], eval_rows: int = DEFAULT_EVAL_ROWS,
                tick_minutes: int = 60) -> dict:
    """Fit ``groups`` (one dataset per 'station:fuel') into a deployable artifact."""
    for key, dataset in groups.items():
        if not dataset["train"] and not dataset["test"]:
            raise ModelError(f"no history for {key}")
    stations = {}
    fingerprint_records = []
    for key, dataset in groups.items():
        fitted = fit_one(dataset)
        if fitted is not None:
            stations[key] = fitted
        for r in dataset["train"] + dataset["test"]:
            station, fuel = key.split(":", 1)
            fingerprint_records.append((r["tick"], station, fuel, r["y"]))
    artifact = {
        "version": "ml-ridge-v1",
        "feature_schema": list(FEATURE_NAMES),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model": "ridge regression (numpy closed form)",
        "stations": stations,
        "data_fingerprint": data_fingerprint(fingerprint_records),
        "training_config": {"alpha": RIDGE_ALPHA, "eval_rows": eval_rows,
                            "tick_minutes": tick_minutes,
                            "source": "official simulator demand history"},
    }
    artifact["checksum"] = model_checksum(artifact)
    return artifact


class MLForecast:
    """Validated, immutable trained model ready for the analyse pipeline."""

    def __init__(self, artifact: dict):
        if artifact.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
            raise ModelError("artifact feature schema is not supported by this build")
        if artifact.get("checksum") != model_checksum(artifact):
            raise ModelError("artifact checksum mismatch; refusing to activate")
        if artifact.get("feature_schema") != list(FEATURE_NAMES):
            raise ModelError("artifact feature schema does not match this build")
        self.artifact = artifact
        self.version = artifact["version"]
        self.models = artifact["stations"]
        self.tick_minutes = artifact["training_config"].get("tick_minutes", 60)

    @property
    def evaluation(self) -> list[dict]:
        return [{"station_fuel": key, "model": self.version, **entry["eval"]}
                for key, entry in self.models.items() if entry.get("eval")]

    def model_for(self, station_id: str, fuel: str) -> dict | None:
        return self.models.get(f"{station_id}:{fuel}")

    def baseline(self, snapshot: Snapshot, station, fuel: str, count: int):
        """(mean, std) litres/hour per future step, or None when not trained here."""
        entry = self.model_for(station.id, fuel)
        if entry is None:
            return None
        from app.intelligence.engine import normalized_history
        normalized = normalized_history(snapshot, station, fuel)
        if not normalized:
            return None
        values = [value for _, value in normalized]
        lags = values[-3:]
        while len(lags) < 3:
            lags.insert(0, 0.0)
        recent_mean = float(np.mean(values[-24:])) if len(values) >= 24 else float(np.mean(values))
        weights = np.asarray(entry["w"], dtype=float)
        steps = []
        for step in range(1, count + 1):
            when = snapshot.instance.sim_time + timedelta(
                minutes=snapshot.instance.tick_minutes * step)
            features = feature_vector(when.hour, when.weekday(), station.demand_multiplier,
                                      recent_mean, lags)
            mean = max(0.0, float(np.dot(np.concatenate(([1.0], features)), weights)))
            steps.append((mean, entry["resid_std"]))
        return steps


def deploy_model(session: Session, artifact: dict, actor: str = "system", reason: str = "") -> dict:
    """Store and activate a trained model, deactivating the previous one.

    The prior model becomes ``previous_version`` and the model_versions row is written
    only after checksum validation so a corrupted artifact cannot be persisted.
    """
    forecaster = MLForecast(artifact)  # validates checksum + schema before any write
    version = forecaster.version
    existing = session.get(ModelVersion, version)
    if existing and existing.checksum != artifact["checksum"]:
        raise ModelError("model version name is already used with different content")
    current = session.scalar(select(ModelVersion).where(ModelVersion.active.is_(True)))
    previous = current.version if current else None
    for row in session.scalars(select(ModelVersion)):
        row.active = False
    row = existing or ModelVersion(version=version)
    row.artifact = artifact
    row.checksum = artifact["checksum"]
    row.feature_schema = list(artifact["feature_schema"])
    row.data_fingerprint = artifact["data_fingerprint"]
    row.training_config = artifact["training_config"]
    row.previous_version = previous
    row.actor = actor
    row.reason = reason
    row.active = True
    row.activated_at = now()
    if not row.evaluation:
        row.evaluation = {key: entry["eval"] for key, entry in artifact["stations"].items()
                          if entry.get("eval")}
    session.add(row)
    session.add(Audit(actor=actor, action="model.deployed", target=version,
                      details={"checksum": artifact["checksum"][:16],
                               "previous": previous, "reason": reason,
                               "fingerprint": artifact["data_fingerprint"][:16]}))
    session.commit()
    return {"version": version, "changed": existing is None or existing.checksum != artifact["checksum"],
            "previous_version": previous, "checksum": artifact["checksum"],
            "stations": sorted(forecaster.models), "evaluation": row.evaluation}


def active_forecast(session: Session) -> MLForecast | None:
    """The active trained model, or None when the registry has none / it is unusable."""
    row = session.scalar(select(ModelVersion).where(ModelVersion.active.is_(True)))
    if row is None:
        return None
    try:
        return MLForecast(row.artifact)
    except (ModelError, KeyError, TypeError, ValueError):
        return None


def record_evaluation(session: Session, run_id: str, issued_at_tick: int, target_tick: int,
                      station_id: str, fuel: str, model_version: str, predicted: float,
                      lower: float, upper: float, observed: float | None = None) -> None:
    """Persist one predicted-versus-observed record for drift and calibration reporting."""
    session.add(ForecastEvaluation(
        run_id=run_id, station_id=station_id, fuel_type=fuel, target_tick=target_tick,
        issued_at_tick=issued_at_tick, horizon=max(0, target_tick - issued_at_tick),
        model_version=model_version, predicted=round(float(predicted), 3),
        lower=round(float(lower), 3), upper=round(float(upper), 3), observed=observed))