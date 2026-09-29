import httpx
import pytest

from app.main import create_app
from app.sim.client import SimError, SimulatorClient


@pytest.mark.parametrize("shape,code", [({"detail": {"code": "ROUTE_DISRUPTED"}}, "ROUTE_DISRUPTED"),
                                      ({"error": {"code": "FAULT_INJECTED"}}, "FAULT_INJECTED"),
                                      ({"detail": [{"msg": "invalid"}]}, "VALIDATION_ERROR")])
async def test_error_shapes_and_no_4xx_retry(config, shape, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(409, json=shape)
    client = SimulatorClient(config, httpx.MockTransport(handler))
    with pytest.raises(SimError) as exc:
        await client.request("GET", "/v1/routes")
    assert exc.value.code == code
    assert len(calls) == 1
    await client.close()


async def test_get_retries_and_stale_headers(config):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(503, json={"error": {"code": "FAULT_INJECTED"}})
        return httpx.Response(200, json=[], headers={"X-Simulator-Stale": "true"})
    client = SimulatorClient(config, httpx.MockTransport(handler))
    data, stale = await client.request("GET", "/v1/routes")
    assert data == [] and stale
    assert len(calls) == 3
    await client.close()


async def test_auth_roles_health_and_dry_run(service, config, fake):
    app = create_app(config, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/network/state")).status_code == 401
        viewer = (await client.post("/api/auth/login", json={"username": "viewer", "password": "demo-viewer"})).json()
        headers = {"Authorization": "Bearer " + viewer["access_token"]}
        assert (await client.post("/api/decide", headers=headers)).status_code == 403
        assert (await client.post("/api/control/sim/reset", headers=headers, json={"confirm": True})).status_code == 403
        operator = (await client.post("/api/auth/login", json={"username": "operator", "password": "demo-operator"})).json()
        headers = {"Authorization": "Bearer " + operator["access_token"]}
        before = len(service.recommendations)
        assert (await client.post("/api/decide?dry_run=true", headers=headers)).status_code == 200
        assert len(service.recommendations) == before
        assert not fake.posts
        fake.stale = True
        await service.refresh()
        assert (await client.get("/api/health/live")).status_code == 200
        assert (await client.get("/api/health/ready")).status_code == 503
        state = (await client.get("/api/network/state", headers=headers)).json()
        assert state["stale"] and len(state["stations"]) == 4
        assert (await client.get("/metrics")).status_code == 200


async def test_expired_snapshot_blocks_and_readiness_recovers(service, fake):
    service.fetched_at -= 100
    assert not service.safe()
    assert service.network()["mode"] == "DEGRADED"
    await service.refresh()
    assert service.safe()
    fake.stale = True
    await service.refresh()
    assert not service.safe()
    fake.stale = False
    await service.refresh()
    assert service.safe()
