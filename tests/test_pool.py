from asyncio import gather, sleep

import httpx
import pytest
import respx

from AsyncShipStation import ConnectionConfig, ShipStationClient, TagsPortal
from AsyncShipStation.common import ShipStationConnection

from .conftest import V1_KEY, V1_SECRET, V2_BASE, V2_KEY


async def test_connect_returns_the_pooled_connection_for_the_same_credentials() -> None:
    first = await ShipStationClient.connect(v2_key=V2_KEY)
    second = await ShipStationClient.connect(v2_key=V2_KEY)
    assert first is second
    assert await ShipStationClient.get_connection(v2_key=V2_KEY) is first


async def test_config_and_credentials_are_part_of_the_identity() -> None:
    base = await ShipStationClient.connect(v2_key=V2_KEY)
    tuned = await ShipStationClient.connect(
        v2_key=V2_KEY, config=ConnectionConfig(timeout=5)
    )
    both = await ShipStationClient.connect(
        v2_key=V2_KEY, v1_key=V1_KEY, v1_secret=V1_SECRET
    )
    assert len({base.pool_key, tuned.pool_key, both.pool_key}) == 3
    assert tuned.config.timeout == 5


async def test_uid_is_a_lookup_handle_separate_from_the_credential_hash(
    v2: ShipStationConnection,
) -> None:
    assert v2.uid != v2.pool_key
    assert await ShipStationClient.get_connection(uid=v2.uid) is v2
    assert await ShipStationClient.connect(uid=v2.uid) is v2


async def test_connect_by_unknown_uid_without_credentials_raises() -> None:
    with pytest.raises(ValueError):
        await ShipStationClient.connect(uid=42)


async def test_a_connection_needs_credentials() -> None:
    with pytest.raises(ValueError):
        ShipStationConnection()
    with pytest.raises(ValueError):
        ShipStationConnection(v1_key=V1_KEY)
    with pytest.raises(ValueError):
        ShipStationConnection(v2_key=V2_KEY, v1_secret=V1_SECRET)


async def test_scoped_clients_share_one_reference_counted_client(
    v2: ShipStationConnection,
) -> None:
    async with ShipStationClient.scoped_client(connection=v2) as outer:
        assert outer is v2 and v2.v2_ref_count == 1 and v2.v2_active
        async with ShipStationClient.scoped_client(v2_key=V2_KEY) as inner:
            assert inner is v2 and v2.v2_ref_count == 2
        assert v2.v2_ref_count == 1 and v2.v2_active

    assert v2.ref_count == 0 and not v2.v2_active
    assert await ShipStationClient.get_connection(uid=v2.uid) is None


async def test_versions_are_counted_separately() -> None:
    both = await ShipStationClient.connect(
        v2_key=V2_KEY, v1_key=V1_KEY, v1_secret=V1_SECRET
    )
    async with ShipStationClient.scoped_client(connection=both, version="both"):
        async with ShipStationClient.scoped_client(connection=both, version="v1"):
            assert both.v1_ref_count == 2 and both.v2_ref_count == 1
        assert both.v1_ref_count == 1 and both.v1_active and both.v2_active

    assert both.ref_count == 0 and not both.v1_active and not both.v2_active


async def test_starting_an_evicted_connection_puts_it_back_in_the_pool(
    v2: ShipStationConnection,
) -> None:
    async with ShipStationClient.scoped_client(connection=v2):
        pass
    assert await ShipStationClient.get_connection(uid=v2.uid) is None

    async with ShipStationClient.scoped_client(connection=v2):
        assert await ShipStationClient.get_connection(uid=v2.uid) is v2


async def test_force_close_drops_every_reference(v2: ShipStationConnection) -> None:
    await ShipStationClient.start(connection=v2, version="v2")
    await ShipStationClient.start(connection=v2, version="v2")
    await ShipStationClient.close(connection=v2, version="v2", force=True)
    assert v2.ref_count == 0 and not v2.v2_active


async def test_close_all_empties_the_pool(v2: ShipStationConnection) -> None:
    await ShipStationClient.start(connection=v2, version="v2")
    await ShipStationClient.close_all()
    assert not v2.v2_active
    assert await ShipStationClient.get_connection(uid=v2.uid) is None


@respx.mock
async def test_concurrent_requests_on_an_unstarted_connection_keep_the_client_open(
    v2: ShipStationConnection,
) -> None:
    arrivals: list[httpx.Request] = []
    open_while_in_flight: list[bool] = []

    async def staggered(request: httpx.Request) -> httpx.Response:
        arrivals.append(request)
        await sleep(0.01 * len(arrivals))
        open_while_in_flight.append(v2.v2_active)
        return httpx.Response(200, json={"tags": []})

    respx.get(f"{V2_BASE}/tags").mock(side_effect=staggered)
    results = await gather(*(TagsPortal.all(v2) for _ in range(5)))

    assert [status for status, _ in results] == [200] * 5
    assert open_while_in_flight == [True] * 5
    assert v2.ref_count == 0 and not v2.v2_active


async def test_debug_toggles_actually_change_state() -> None:
    try:
        assert ShipStationClient.debug_on() is True
        assert ShipStationClient._debug is True
        assert ShipStationClient.debug_off() is False
        assert ShipStationClient._debug is False
    finally:
        ShipStationClient.debug_off()
