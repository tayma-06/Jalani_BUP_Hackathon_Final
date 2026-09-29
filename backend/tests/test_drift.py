from types import SimpleNamespace

from app.intelligence.drift import DriftMonitor


def snapshot(tick, demand):
    return SimpleNamespace(instance=SimpleNamespace(tick=tick), history=[SimpleNamespace(
        station_id="s", fuel_type="DIESEL", tick=tick, demand_liters=demand)])


def test_drift_requires_out_of_sample_evidence_and_recovers():
    monitor = DriftMonitor(window=4)
    prediction = {("s", "DIESEL"): {"mean": [100], "std": [10]}}
    assert monitor.observe(snapshot(1, 500)) == []
    for tick in range(1, 9):
        monitor.issue(tick, prediction)
        alerts = monitor.observe(snapshot(tick + 1, 150 if tick <= 4 else 100))
        if tick < 4:
            assert alerts == []
        if tick == 4:
            assert len(alerts) == 1
        # Duplicate reads must not score the same forecast twice.
        before = list(monitor.residuals[("s", "DIESEL")])
        monitor.observe(snapshot(tick + 1, 999))
        assert list(monitor.residuals[("s", "DIESEL")]) == before
    assert alerts == []


def test_missing_history_is_not_zero_demand_and_forecasts_are_immutable():
    monitor = DriftMonitor(window=2)
    monitor.issue(1, {("s", "DIESEL"): {"mean": [100], "std": [10]}})
    monitor.issue(1, {("s", "DIESEL"): {"mean": [900], "std": [10]}})
    assert monitor.pending[("s", "DIESEL", 2)] == (100, 10)
    monitor.observe(SimpleNamespace(instance=SimpleNamespace(tick=3), history=[]))
    assert monitor.residuals == {} and monitor.pending == {}
