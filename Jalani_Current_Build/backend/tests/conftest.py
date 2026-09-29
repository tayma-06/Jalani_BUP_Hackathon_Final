import pytest

from app.config import Settings
from app.service import FuelService
from app.sim.client import SimulatorClient
from tests.fake_simulator import FakeSimulator


@pytest.fixture
def config(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", background_enabled=False, stream_enabled=False,
                    retry_base_seconds=0, breaker_seconds=0.01, monte_carlo_paths=60)


@pytest.fixture
def fake():
    return FakeSimulator()


@pytest.fixture
async def service(config, fake):
    service = FuelService(config, SimulatorClient(config, transport=fake.transport()))
    await service.refresh()
    yield service
    await service.client.close()
    service.db.engine.dispose()
