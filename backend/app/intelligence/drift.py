"""Prequential drift monitoring: score forecasts issued before observations exist."""
from collections import deque

import numpy as np


class DriftMonitor:
    def __init__(self, window=16, threshold=2.0):
        self.window = window
        self.threshold = threshold
        self.pending = {}
        self.residuals = {}

    def observe(self, snapshot):
        for row in snapshot.history:
            key = (row.station_id, row.fuel_type)
            prediction = self.pending.pop((*key, row.tick), None)
            if prediction is None:
                continue
            mean, std = prediction
            values = self.residuals.setdefault(key, deque(maxlen=self.window))
            values.append((row.demand_liters - mean) / max(std, mean * 0.05, 1.0))
        # Missing observations cannot be replaced with zero demand.
        self.pending = {k: v for k, v in self.pending.items() if k[2] > snapshot.instance.tick}
        alerts = []
        for (station, fuel), values in self.residuals.items():
            if len(values) < self.window:
                continue
            bias = float(np.mean(values))
            if abs(bias) >= self.threshold:
                alerts.append({"key": f"forecast_drift:{station}:{fuel}", "type": "forecast_drift",
                               "entity_id": station, "fuel_type": fuel, "severity": "WARNING",
                               "message": f"Forecast bias is {bias:.2f} standard deviations over "
                                          f"{len(values)} scored observations; review demand model.",
                               "last_tick": snapshot.instance.tick, "event_ids": []})
        return alerts

    def issue(self, tick, analysis):
        for key, prediction in analysis.items():
            for step, (mean, std) in enumerate(zip(prediction["mean"], prediction["std"]), 1):
                # Keep the first issued forecast; repeated paused polls cannot rewrite it.
                self.pending.setdefault((*key, tick + step), (mean, std))
