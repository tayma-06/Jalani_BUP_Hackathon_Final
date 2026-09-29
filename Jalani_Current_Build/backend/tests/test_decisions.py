import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db import Execution
from app.service import FuelService
from app.sim.client import SimulatorClient


async def test_concurrent_approvals_create_one_durable_intent(service, fake):
    assert service.safe()
    rec = service.recommendations[0]
    first, second = await asyncio.gather(service.approve(rec["id"], "alice"), service.approve(rec["id"], "alice"))
    assert first["id"] == second["id"]
    assert len(fake.posts) == 1
    with service.db.session() as session:
        assert session.scalar(select(func.count()).select_from(Execution)) == 1


async def test_changed_body_cannot_reuse_existing_intent(service, fake):
    rec = service.recommendations[0]
    await service.approve(rec["id"], "alice")
    with pytest.raises(HTTPException, match="different quantity"):
        await service.approve(rec["id"], "alice", 501)
    assert len(fake.posts) == 1


async def test_lost_accepted_response_reconciles_after_restart(service, fake, config):
    rec = service.recommendations[0]
    fake.lose_response = True
    result = await service.approve(rec["id"], "alice")
    assert result["status"] == "UNKNOWN"
    assert not service.safe()
    fake.unavailable = False
    fake.lose_response = False
    restarted = FuelService(config, SimulatorClient(config, transport=fake.transport()))
    assert await restarted.refresh()
    assert restarted.safe()
    assert restarted.decisions[0]["sim_id"] == 1
    assert restarted.decisions[0]["request"] == fake.posts[0]
    assert len(fake.posts) == 1
    await restarted.client.close()
    restarted.db.engine.dispose()


@pytest.mark.parametrize("fault", ["stale", "unavailable", "moving"])
async def test_bad_snapshots_block_manual_and_auto(service, fake, fault):
    rec = service.recommendations[0]
    setattr(fake, fault, True)
    for actor in ["alice", "AUTOPILOT"]:
        with pytest.raises(HTTPException):
            await service.approve(rec["id"], actor)
    assert not fake.posts
    assert service.network()["stations"]
    assert service.network()["execution_blocked"]


async def test_db_failure_blocks_post_but_preserves_cache(service, fake, monkeypatch):
    rec = service.recommendations[0]
    def broken():
        raise OperationalError("offline", {}, Exception("offline"))
    monkeypatch.setattr(service.db, "check", broken)
    with pytest.raises(HTTPException) as exc:
        await service.approve(rec["id"], "alice")
    assert exc.value.status_code == 503
    assert not fake.posts
    assert len(service.network()["stations"]) == 4


async def test_definitive_rejection_does_not_become_unknown(service, fake):
    fake.reject_code = "ROUTE_DISRUPTED"
    result = await service.approve(service.recommendations[0]["id"], "alice")
    assert result["status"] == "FAILED"
    assert service.safe()
    assert len(fake.posts) == 1


async def test_paused_changes_propagate_and_invalid_response_keeps_cache(service, fake):
    tick = service.snapshot.instance.tick
    fake.data["stations"][0]["inventory"]["DIESEL"] = 1000
    await service.refresh()
    assert service.snapshot.instance.tick == tick
    assert service.network()["stations"][0]["fuels"]["DIESEL"]["inventory"] == 1000
    fake.data["stations"][0]["inventory"]["DIESEL"] = -100
    assert not await service.refresh()
    assert service.network()["stations"][0]["fuels"]["DIESEL"]["inventory"] == 1000
    assert service.stale


async def test_reset_changes_run_and_abandons_old_pending_intent(service, fake):
    await service.approve(service.recommendations[0]["id"], "alice")
    old_run = service.run_id
    fake.data["instance"]["tick"] = 0
    fake.data["allocations"] = []
    await service.refresh()
    assert service.run_id != old_run
    assert service.decisions[0]["status"] == "ABANDONED_RESET"
    assert len(fake.posts) == 1


async def test_reset_notice_detects_identical_tick_reset(service):
    old_run = service.run_id
    service.reset_notice = True
    await service.refresh()
    assert old_run != service.run_id


async def test_crisis_requires_human_review_even_with_auto(service, fake):
    service.autopilot = "auto"
    rec = service.recommendations[0]
    from app.db import Recommendation
    with service.db.session() as session:
        row = session.get(Recommendation, rec["id"])
        row.payload = {**row.payload, "requires_review": True}
        session.commit()
    with pytest.raises(HTTPException, match="Human review"):
        await service.approve(rec["id"], "AUTOPILOT")
    assert not fake.posts
