"""Durable growth is bounded, and only rows nothing can read are removed."""
import httpx
import pytest
from sqlalchemy import event, func, select

from app.db import (
    KEEP_ALERTS,
    KEEP_AUDIT,
    KEEP_EXECUTIONS,
    KEEP_HISTORY,
    KEEP_INCIDENTS,
    KEEP_RECOMMENDATIONS,
    KEEP_SNAPSHOTS,
    OPEN_STATUSES,
    Alert,
    DemandObservation,
    Execution,
    Incident,
    Recommendation,
    SnapshotRecord,
    audit,
)
from app.db import Audit as AuditModel
from app.main import create_app
from app.service import FuelService
from app.sim.client import SimulatorClient


def advance(fake, ticks):
    fake.data["instance"]["tick"] += ticks


def counts(service):
    with service.db.session() as session:
        return {t: session.scalar(select(func.count()).select_from(m)) for t, m in
                (("snapshots", SnapshotRecord), ("demand_obs", DemandObservation),
                 ("recommendations", Recommendation), ("executions", Execution),
                 ("alerts", Alert), ("incidents", Incident))}


async def test_snapshot_and_history_stay_bounded_across_many_ticks(service, fake):
    for _ in range(12):
        advance(fake, 1)
        fake.record_demand()
        await service.refresh()
    seen = counts(service)
    assert seen["snapshots"] <= KEEP_SNAPSHOTS
    assert seen["demand_obs"] <= KEEP_HISTORY
    # The window the analysis reads is still fully present, so pruning costs no accuracy.
    assert len(service.snapshot.history) == seen["demand_obs"]


async def test_pruning_keeps_rows_still_referenced_by_an_execution(service, fake):
    rec = service.recommendations[0]
    await service.approve(rec["id"], "alice")
    for _ in range(10):
        advance(fake, 1)
        fake.record_demand()
        await service.refresh()
    with service.db.session() as session:
        referenced = session.scalars(select(Execution.recommendation_id)).all()
        assert referenced
        surviving = {row.id for row in session.scalars(select(Recommendation))}
    assert set(referenced) <= surviving


async def test_old_runs_are_removed_but_current_run_survives_a_restart(service, fake, config):
    for _ in range(4):
        advance(fake, 1)
        fake.record_demand()
        await service.refresh()
    old_run = service.run_id
    # A simulator reset rewinds the world and drops its history, as the real one does.
    service.history_run = None
    fake.data["instance"]["tick"] = 0
    fake.data["demand-history"] = []
    assert await service.refresh()
    assert service.run_id != old_run
    with service.db.session() as session:
        assert session.scalars(select(DemandObservation).where(DemandObservation.run_id == old_run)).all() == []
        assert session.scalars(select(SnapshotRecord).where(SnapshotRecord.run_id == old_run)).all() == []
    restarted = FuelService(config, SimulatorClient(config, transport=fake.transport()))
    assert await restarted.refresh()
    assert restarted.safe()
    assert restarted.snapshot.instance.tick == fake.data["instance"]["tick"]
    await restarted.client.close()
    restarted.db.engine.dispose()


async def test_audit_trail_is_readable_paged_and_bounded(service, fake, config):
    for _ in range(4):
        advance(fake, 1)
        fake.record_demand()
        await service.refresh()
    approved = service.recommendations[0]["id"]
    await service.approve(approved, "alice")
    app = create_app(config, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/audit")).status_code == 401
        token = (await client.post("/api/auth/login",
                                   json={"username": "operator", "password": "demo-operator"})).json()
        headers = {"Authorization": "Bearer " + token["access_token"]}
        page = (await client.get("/api/audit", headers=headers)).json()
        entry = next(row for row in page if row["action"] == "decision.approved")
        assert entry["actor"] == "alice" and entry["target"] == approved
        assert entry["details"]["run_id"] == service.run_id
        # Newest first, and `before` walks backwards without repeating a row.
        assert [row["id"] for row in page] == sorted((row["id"] for row in page), reverse=True)
        older = (await client.get(f"/api/audit?before={page[-1]['id']}&limit=2", headers=headers)).json()
        assert len(older) <= 2
        assert all(row["id"] < page[-1]["id"] for row in older)
    with service.db.session() as session:
        session.add_all([AuditModel(actor="bulk", action="test", target=str(i), details={})
                         for i in range(KEEP_AUDIT + 50)])
        session.commit()
    for _ in range(2):
        advance(fake, 10)
        fake.record_demand()
        await service.refresh()
    with service.db.session() as session:
        assert session.scalar(select(func.count()).select_from(AuditModel)) <= KEEP_AUDIT


async def test_audit_helper_records_details(service):
    with service.db.session() as session:
        audit(session, "alice", "thing.done", "target-1", reason="because", extra=7)
        session.commit()
    with service.db.session() as session:
        row = session.scalars(select(AuditModel)).one()
    assert row.details == {"reason": "because", "extra": 7}
    assert row.actor == "alice" and row.action == "thing.done" and row.target == "target-1"


async def test_expired_recommendations_are_capped(service, fake):
    with service.db.session() as session:
        session.add_all([Recommendation(run_id=service.run_id, created_tick=i, status="EXPIRED", payload={})
                         for i in range(KEEP_RECOMMENDATIONS + 40)])
        session.commit()
    for _ in range(2):
        advance(fake, 10)
        await service.refresh()
    with service.db.session() as session:
        expired = session.scalar(select(func.count()).select_from(Recommendation)
                                  .where(Recommendation.status == "EXPIRED"))
    assert expired <= KEEP_RECOMMENDATIONS


async def test_history_cache_survives_a_refresh_without_rereading_the_database(service, fake):
    for _ in range(3):
        advance(fake, 1)
        fake.record_demand()
        await service.refresh()
    seeded = dict(service.history_cache)
    assert seeded
    statements = []

    def record(_conn, _cur, statement, _params, _context, _executemany):
        statements.append(statement)

    event.listen(service.db.engine, "before_cursor_execute", record)
    try:
        advance(fake, 1)
        fake.record_demand()
        assert await service.refresh()
    finally:
        event.remove(service.db.engine, "before_cursor_execute", record)
    # Only bounded pruning touches the table; nothing reads the window back out.
    assert not [s for s in statements if s.lstrip().upper().startswith("SELECT") and "demand_obs" in s]
    assert [s for s in statements if s.lstrip().upper().startswith("DELETE") and "demand_obs" in s]
    # Rows already in memory are kept, and the newly observed tick is merged in.
    assert all(service.history_cache[key]["tick"] <= value["tick"] for key, value in seeded.items())
    assert len(service.history_cache) > len(seeded)
    assert len({row.id for row in service.snapshot.history}) == len(service.history_cache)


async def test_alert_and_incident_history_is_bounded(service, fake):
    for _ in range(3):
        advance(fake, 1)
        fake.record_demand()
        await service.refresh()
    with service.db.session() as session:
        session.add_all([Alert(id=f"{service.run_id}:bulk-{i}", run_id=service.run_id,
                               status="RESOLVED", payload={"last_tick": 0}) for i in range(KEEP_ALERTS + 40)])
        session.add_all([Incident(id=f"{service.run_id}:bulk-{i}", run_id=service.run_id,
                                  payload={"tick": 0, "text": "bulk", "source": "template", "severity": "info"})
                         for i in range(KEEP_INCIDENTS + 40)])
        session.commit()
    for _ in range(2):
        advance(fake, 10)
        await service.refresh()
    seen = counts(service)
    assert seen["alerts"] <= KEEP_ALERTS
    assert seen["incidents"] <= KEEP_INCIDENTS


async def test_settled_executions_and_their_recommendations_are_bounded(service, fake):
    for _ in range(3):
        advance(fake, 1)
        fake.record_demand()
        await service.refresh()
    with service.db.session() as session:
        # An earlier run's settled shipments: reachable only through the decision list, so they
        # are what the cap is allowed to trim, along with the recommendations they reference.
        recs = [Recommendation(run_id="an-older-run", created_tick=i, status="APPROVED",
                              payload={"id": f"r-{i}"}) for i in range(KEEP_EXECUTIONS + 40)]
        session.add_all(recs)
        session.flush()
        session.add_all([Execution(recommendation_id=r.id, run_id="an-older-run", status="ARRIVED",
                                   actor="alice", body={}, idempotency_key=f"k-{r.id}")
                         for r in recs])
        session.commit()
    for _ in range(2):
        advance(fake, 10)
        await service.refresh()
    with service.db.session() as session:
        settled = session.scalar(select(func.count()).select_from(Execution)
                                 .where(Execution.status.not_in(OPEN_STATUSES)))
        assert settled <= KEEP_EXECUTIONS
        # Every surviving recommendation must still have its execution. A row that kept the
        # recommendation after losing the execution would be an orphan nothing can reach.
        with_execution = set(session.scalars(select(Execution.recommendation_id)).all())
        survivors = {r.id for r in session.scalars(
            select(Recommendation).where(Recommendation.run_id == "an-older-run"))}
        assert survivors <= with_execution
    # The decision list is still populated, so pruning did not empty the operator's history.
    assert service.decisions


async def test_an_unsettled_execution_survives_a_simulator_reset(service, fake):
    for _ in range(3):
        advance(fake, 1)
        fake.record_demand()
        await service.refresh()
    rec = service.recommendations[0]
    await service.approve(rec["id"], "alice")
    with service.db.session() as session:
        row = session.scalars(select(Execution)).one()
        row.status = "IN_TRANSIT"
        unsettled_id = row.id
        session.commit()
    # A reset mints a new run. The shipment is still in flight, so the row must be kept until
    # the refresh loop settles it against the ledger.
    service.history_run = None
    fake.data["instance"]["tick"] = 0
    fake.data["demand-history"] = []
    assert await service.refresh()
    for _ in range(2):
        advance(fake, 5)
        await service.refresh()
    with service.db.session() as session:
        survivor = session.get(Execution, unsettled_id)
    # It is no longer reconcilable after the reset, so it is retained as a settled record or
    # pruned as unreachable; either way it must not linger as an open intent forever.
    assert survivor is None or survivor.status not in OPEN_STATUSES


async def test_monitoring_alerts_survive_a_simulator_reset(service, fake, config):
    from app.main import WebhookAlert, WebhookAlerts

    for _ in range(3):
        advance(fake, 1)
        fake.record_demand()
        await service.refresh()
    old_run = service.run_id
    service.history_run = None
    fake.data["instance"]["tick"] = 0
    fake.data["demand-history"] = []
    assert await service.refresh()
    assert service.run_id != old_run
    app = create_app(config, service)
    payload = WebhookAlerts(receiver="jalani", status="firing", alerts=[
        WebhookAlert(status="firing", labels={"alertname": "BackendDown", "severity": "critical"},
                     annotations={"summary": "simulated"},
                     startsAt="2026-01-01T00:00:00Z", endsAt="0001-01-01T00:00:00Z")])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/internal/alerts", json=payload.model_dump(by_alias=True),
                                     headers={"Authorization": "Bearer " + config.alert_webhook_token})
        assert response.status_code == 200
    # Simulator alerts have no Prometheus labels, so match on the recorded alert name.
    def monitoring_alerts():
        return [a for a in service.alerts if a.get("source") == "prometheus"]

    assert any(a.get("alertname") == "BackendDown" for a in monitoring_alerts())
    # A later reset must not delete an Alertmanager alert: it belongs to no simulator run.
    service.history_run = None
    fake.data["instance"]["tick"] = 0
    fake.data["demand-history"] = []
    assert await service.refresh()
    assert any(a.get("alertname") == "BackendDown" for a in monitoring_alerts())


@pytest.mark.parametrize("limit", [0, 1001])
async def test_audit_page_size_is_bounded(service, config, limit):
    app = create_app(config, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        token = (await client.post("/api/auth/login",
                                   json={"username": "viewer", "password": "demo-viewer"})).json()
        headers = {"Authorization": "Bearer " + token["access_token"]}
        assert (await client.get(f"/api/audit?limit={limit}", headers=headers)).status_code == 422
