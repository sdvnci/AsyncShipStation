from base64 import b64encode
from collections.abc import Iterator
from typing import Any, cast

import httpx
import pytest
import respx

from AsyncShipStation import (
    DownloadPortal,
    ErrorResponse,
    OrderPortal,
    RateLimitError,
    ShipmentPortal,
    TagsPortal,
)
from AsyncShipStation.common import Error, ShipStationConnection

from .conftest import V1_BASE, V1_KEY, V1_SECRET, V2_BASE, V2_KEY


@pytest.fixture(autouse=True)
def mock_api() -> Iterator[None]:
    with respx.mock:
        yield


def error_of(body: object) -> Error:
    return cast(ErrorResponse, body)["errors"][0]


async def test_v2_connection_sends_the_api_key(v2: ShipStationConnection) -> None:
    route = respx.get(f"{V2_BASE}/tags").respond(json={"tags": []})
    await TagsPortal.all(v2)

    headers = route.calls.last.request.headers
    assert headers["api-key"] == V2_KEY
    assert "Authorization" not in headers


async def test_v1_connection_sends_basic_auth(v1: ShipStationConnection) -> None:
    route = respx.get(f"{V1_BASE}/orders").respond(json={"orders": []})
    await OrderPortal.where(v1)

    expected = b64encode(f"{V1_KEY}:{V1_SECRET}".encode()).decode()
    headers = route.calls.last.request.headers
    assert headers["Authorization"] == f"Basic {expected}"
    assert "api-key" not in headers


async def test_shipstation_errors_keep_their_status_and_body(
    v2: ShipStationConnection,
) -> None:
    body = {
        "request_id": "req-1",
        "errors": [
            {
                "error_source": "ShipStation",
                "error_type": "validation",
                "error_code": "invalid_field_value",
                "message": "tag_name is required",
            }
        ],
    }
    respx.get(f"{V2_BASE}/tags").respond(400, json=body)
    status, response = await TagsPortal.all(v2)

    assert status == 400 and response == body


async def test_v1_error_bodies_keep_their_status(v1: ShipStationConnection) -> None:
    respx.get(f"{V1_BASE}/orders").respond(
        401, json={"Message": "Authorization has been denied for this request."}
    )
    status, response = await OrderPortal.where(v1)

    assert status == 401
    assert "Authorization has been denied" in error_of(response)["message"]


async def test_empty_error_bodies_keep_their_status(v2: ShipStationConnection) -> None:
    respx.get(f"{V2_BASE}/tags").respond(404)
    status, response = await TagsPortal.all(v2)

    assert status == 404
    assert error_of(response)["message"] == "Not Found"


async def test_order_portal_raises_v1_rate_limits_with_the_reset_window(
    v1: ShipStationConnection,
) -> None:
    respx.get(f"{V1_BASE}/orders").respond(
        429, text="Too Many Requests", headers={"X-Rate-Limit-Reset": "37"}
    )
    with pytest.raises(RateLimitError) as caught:
        await OrderPortal.where(v1)

    assert caught.value.status_code == 429
    assert caught.value.retry_after == 37.0
    assert caught.value.details == "Too Many Requests"


async def test_retry_after_takes_precedence_over_the_v1_reset_header(
    v1: ShipStationConnection,
) -> None:
    respx.get(f"{V1_BASE}/orders").respond(
        429, headers={"Retry-After": "5", "X-Rate-Limit-Reset": "37"}
    )
    with pytest.raises(RateLimitError) as caught:
        await OrderPortal.where(v1)

    assert caught.value.retry_after == 5.0


async def test_unreadable_rate_limit_headers_leave_retry_after_unset(
    v1: ShipStationConnection,
) -> None:
    respx.get(f"{V1_BASE}/orders").respond(
        429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    )
    with pytest.raises(RateLimitError) as caught:
        await OrderPortal.where(v1)

    assert caught.value.retry_after is None


async def test_other_portals_return_rate_limits_as_a_429(
    v2: ShipStationConnection,
) -> None:
    respx.get(f"{V2_BASE}/tags").respond(429, headers={"Retry-After": "12"})
    status, response = await TagsPortal.all(v2)

    assert status == 429
    assert error_of(response)["message"] == "Too Many Requests"


async def test_non_json_error_bodies_keep_their_status(
    v2: ShipStationConnection,
) -> None:
    respx.get(f"{V2_BASE}/tags").respond(502, text="<html>Bad Gateway</html>")
    status, response = await TagsPortal.all(v2)

    assert status == 502
    assert "Bad Gateway" in error_of(response)["message"]


async def test_a_non_json_success_body_is_a_500(v2: ShipStationConnection) -> None:
    respx.get(f"{V2_BASE}/tags").respond(200, text="not json")
    status, response = await TagsPortal.all(v2)

    assert status == 500
    assert error_of(response)["message"].startswith("Could not decode")


async def test_a_version_without_credentials_fails_locally(
    v2: ShipStationConnection,
) -> None:
    route = respx.get(f"{V1_BASE}/orders").respond(json={"orders": []})
    status, response = await OrderPortal.where(v2)

    assert status == 400
    assert "v1 is not enabled" in error_of(response)["message"]
    assert not route.called


async def test_transport_failures_are_a_500_naming_the_exception(
    v2: ShipStationConnection,
) -> None:
    respx.get(f"{V2_BASE}/tags").mock(side_effect=httpx.ConnectError("refused"))
    status, response = await TagsPortal.all(v2)

    assert status == 500
    assert error_of(response)["message"] == "ConnectError: refused"


async def test_identity_tags_the_response_and_each_shipment(
    v2: ShipStationConnection,
) -> None:
    respx.get(url__startswith=f"{V2_BASE}/shipments").respond(
        json={"shipments": [{"shipment_id": "se-1"}, {"shipment_id": "se-2"}]}
    )
    status, response = await ShipmentPortal.where(v2, identity=True)

    body = cast(dict[str, Any], response)
    assert status == 200
    assert body["__kind__"] == "ShipmentListResponse"
    assert [s["__kind__"] for s in body["shipments"]] == ["Shipment", "Shipment"]


async def test_download_errors_keep_their_status(v2: ShipStationConnection) -> None:
    respx.get(url__startswith=f"{V2_BASE}/downloads/").respond(404, text="Not Found")
    status, response = await DownloadPortal.download_file(
        v2, "labels", "abc", "label.pdf"
    )

    assert status == 404
    assert "Not Found" in error_of(response)["message"]


async def test_download_returns_the_file_bytes(v2: ShipStationConnection) -> None:
    respx.get(url__startswith=f"{V2_BASE}/downloads/").respond(200, content=b"%PDF-1.7")
    status, response = await DownloadPortal.download_file(
        v2, "labels", "abc", "label.pdf"
    )

    assert status == 200 and response == b"%PDF-1.7"
