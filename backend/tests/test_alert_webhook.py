"""Alert delivery must remain readable while the simulator is degraded."""
from uuid import uuid4

import httpx
import pytest

from app.db import Alert
from app.main import create_app


def payload(status="firing", **labels):
    return {"alerts": [{"status": status,
                        "labels": {"alertname": "StaleSimulatorData", "severity": "warning", **labels},
                        "annotations": {"summary": "Data freshness gate is blocking allocation execution"},
                        "startsAt": "2026-09-29T00:00:00Z"}]}


async def sign_in(client, role="viewer"):
    response = await client.post("/api/auth/login", json={"username": role, "password": f"demo-{role}"})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.mark.parametrize("authorization", [None, "Bearer incorrect-token", "Basic demo-alert-webhook-token"])
async def test_webhook_rejects_missing_or_invalid_credentials(service, config, authorization):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(config, service)),
                                base_url="http://test") as client:
        headers = {"Authorization": authorization} if authorization else {}
        response = await client.post("/api/internal/alerts", json=payload(), headers=headers)
        assert response.status_code == 401
        assert not any(a.get("source") == "prometheus" for a in service.alerts)


async def test_webhook_firing_and_resolved_are_immediately_visible_with_audit(service, config):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(config, service)),
                                base_url="http://test") as client:
        viewer = await sign_in(client)
        sender = {"Authorization": "Bearer " + config.alert_webhook_token}
        before = service.network()["kpis"]["open_alerts"]
        response = await client.post("/api/internal/alerts", json=payload(), headers=sender)
        assert response.status_code == 200
        key = response.json()["accepted"][0]
        rows = (await client.get("/api/alerts", headers=viewer)).json()
        row = next(a for a in rows if a["id"] == key)
        assert row["status"] == "OPEN"
        assert row["message"] and row["type"] == "StaleSimulatorData"
        assert row["event_ids"] == []  # The Alerts UI reads this array without a fallback.
        assert row["first_tick"] == row["last_tick"] == service.snapshot.instance.tick
        assert service.network()["kpis"]["open_alerts"] == before + 1
        response = await client.post("/api/internal/alerts", json=payload("resolved"), headers=sender)
        assert response.json()["accepted"] == [key]
        rows = (await client.get("/api/alerts?status=RESOLVED", headers=viewer)).json()
        assert any(a["id"] == key for a in rows)
        assert service.network()["kpis"]["open_alerts"] == before
        audit = (await client.get("/api/audit", headers=viewer)).json()
        assert [a["action"] for a in audit if a["target"] == key] == ["alert.resolved", "alert.firing"]


async def test_prometheus_alert_survives_refresh_and_simulator_reset(service, config, fake):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(config, service)),
                                base_url="http://test") as client:
        sender = {"Authorization": "Bearer " + config.alert_webhook_token}
        key = (await client.post("/api/internal/alerts", json=payload(), headers=sender)).json()["accepted"][0]
        fake.data["instance"]["tick"] += 8
        await service.refresh()
        assert service.safe()
        assert next(a for a in service.alerts if a["id"] == key)["status"] == "OPEN"
        service.run_id = str(uuid4())
        service.load_views()
        assert next(a for a in service.alerts if a["id"] == key)["status"] == "OPEN"
        operator = await sign_in(client, "operator")
        response = await client.post(f"/api/alerts/{key}/ack", headers=operator)
        assert response.status_code == 200
        await client.post("/api/internal/alerts", json=payload(), headers=sender)
        assert next(a for a in service.alerts if a["id"] == key)["status"] == "ACKNOWLEDGED"


async def test_monitoring_does_not_share_identity_between_label_sets(service, config):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(config, service)),
                                base_url="http://test") as client:
        sender = {"Authorization": "Bearer " + config.alert_webhook_token}
        keys = []
        for instance in ("backend-one", "backend-two"):
            response = await client.post("/api/internal/alerts", json=payload(instance=instance), headers=sender)
            keys.extend(response.json()["accepted"])
        assert len(set(keys)) == 2
        await client.post("/api/internal/alerts", json=payload("resolved", instance="backend-one"), headers=sender)
        assert {a["id"]: a["status"] for a in service.alerts if a["id"] in keys} == {
            keys[0]: "RESOLVED", keys[1]: "OPEN"}


async def test_delivery_remains_visible_during_failed_simulator_reads(service, config, fake):
    fake.unavailable = True
    await service.refresh()
    assert not service.safe()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(config, service)),
                                base_url="http://test") as client:
        sender = {"Authorization": "Bearer " + config.alert_webhook_token}
        key = (await client.post("/api/internal/alerts", json=payload(), headers=sender)).json()["accepted"][0]
        assert next(a for a in service.alerts if a["id"] == key)["status"] == "OPEN"


async def test_legacy_monitoring_rows_are_not_expired_by_simulator_ticks(service):
    with service.db.session() as session:
        session.add(Alert(id="prometheus:Legacy:warning", run_id=service.run_id, status="OPEN",
                          payload={"source": "prometheus", "alertname": "Legacy"}))
        session.commit()
    # Legacy webhook rows lack last_tick, which previously crashed this refresh.
    await service.refresh()
    assert service.safe()


async def test_webhook_rejects_unrenderable_labels(service, config):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(config, service)),
                                base_url="http://test") as client:
        response = await client.post("/api/internal/alerts", json=payload(severity={"invalid": "object"}),
                                     headers={"Authorization": "Bearer " + config.alert_webhook_token})
        assert response.status_code == 422
