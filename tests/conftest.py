from collections.abc import AsyncGenerator

import pytest

from AsyncShipStation import ShipStationClient
from AsyncShipStation.common import ShipStationConnection

V2_KEY = "v2-api-key"
V1_KEY = "v1-api-key"
V1_SECRET = "v1-api-secret"
V2_BASE = "https://api.shipstation.com/v2"
V1_BASE = "https://ssapi.shipstation.com"


@pytest.fixture(autouse=True)
async def clean_pool() -> AsyncGenerator[None, None]:
    yield
    await ShipStationClient.close_all()


@pytest.fixture
async def v2() -> ShipStationConnection:
    return await ShipStationClient.connect(v2_key=V2_KEY)


@pytest.fixture
async def v1() -> ShipStationConnection:
    return await ShipStationClient.connect(v1_key=V1_KEY, v1_secret=V1_SECRET)
