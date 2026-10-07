# ShipStation Interaction / Automation
[![Type-Check](https://github.com/sudoDeVinci/AsyncShipStation/actions/workflows/mypy.yml/badge.svg?branch=main)](https://github.com/sudoDeVinci/AsyncShipStation/actions/workflows/mypy.yml)
[![Linting](https://github.com/sudoDeVinci/AsyncShipStation/actions/workflows/linting.yml/badge.svg?branch=main)](https://github.com/sudoDeVinci/AsyncShipStation/actions/workflows/linting.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Validation: Pydantic v2](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/pydantic/pydantic/main/docs/badge/v2.json)](https://pydantic.dev)
[![Python 3.11.14](https://img.shields.io/badge/python-3.11.14-blue.svg)](https://www.python.org/downloads/)
![PyPI - Version](https://img.shields.io/pypi/v/AsyncShipStation)
![PyPI - License](https://img.shields.io/pypi/l/AsyncShipStation)

Async Python client for ShipStation v1 and v2 APIs with an emphasis on typing.

## Install

### pip

~~~bash
pip install AsyncShipStation
~~~

### Manual

~~~bash
git clone git@github.com:sudoDeVinci/AsyncShipStation.git
cd AsyncShipStation
pip install -r requirements.txt
~~~

## Quick Start

Create a connection, then pass it to the portal you want to use.

~~~python
import asyncio

from AsyncShipStation import ShipStationClient, ShipmentPortal


async def main() -> None:
    connection = await ShipStationClient.connect(
        v2_key="your_v2_api_key",
        v1_key="your_v1_api_key",
        v1_secret="your_v1_secret",
    )

    async with ShipStationClient.scoped_client(connection=connection, version="v2"):
        status, shipments = await ShipmentPortal.where(
            connection,
            page_size=10,
            page=1,
        )
        print(status, shipments)


if __name__ == "__main__":
    asyncio.run(main())
~~~

The library comes pre-configured with 'sensible' default values for connections, but you can configure these parameters by passing a `ConnectionConfig` object with the parameters you would like changed.

~~~python
from AsyncShipStation import ConnectionConfig, ShipStationClient

config  = ConnectionConfig(
    version="v2",
    timeout=30,
    max_connections=20,
    max_keepalive_connections=10,
    http2=False,
    retries=4,
    user_agent="your_custom_UA_string",
)
connection = await ShipStationClient.connect(
    v2_key="your_v2_api_key",
    v1_key="your_v1_api_key",
    v1_secret="your_v1_secret",
    config=config,
    )
~~~

**NOTE:* A connection's config is tied to its identity. Two connections to the ssame credentials, if using different configs, will be treated as separate connections.*

## Client Lifecycle

### Use the async context manager w/ existing connection object

Use `scoped_client()` when you want the connection lifecycle handled for you.

~~~python
import asyncio
import os

from dotenv import load_dotenv

from AsyncShipStation import ShipStationClient, ShipmentPortal

load_dotenv()
V2_API_KEY: str | None = os.getenv("SHIP_STATION_V2")
V1_API_KEY: str | None = os.getenv("SHIP_STATION_V1")
V1_SECRET: str | None = os.getenv("SHIP_STATION_SECRET")


async def main() -> None:
    connection = await ShipStationClient.connect(
        v2_key=V2_API_KEY or "",
        v1_key=V1_API_KEY,
        v1_secret=V1_SECRET,
    )

    async with ShipStationClient.scoped_client(connection=connection, version="v2"):
        status, shipments = await ShipmentPortal.where(connection, page_size=10, page=1)
        print(status, shipments)


if __name__ == "__main__":
    asyncio.run(main())
~~~

### Use the async context manager w/o existing connection object

If you don't need to reuse the connection object, you can skip the explicit configuration step and pass the credentials directly to `scoped_client()`.
~~~python
import asyncio
import os

from dotenv import load_dotenv

from AsyncShipStation import ShipStationClient, ShipmentPortal

load_dotenv()
V2_API_KEY: str | None = os.getenv("SHIP_STATION_V2")
V1_API_KEY: str | None = os.getenv("SHIP_STATION_V1")
V1_SECRET: str | None = os.getenv("SHIP_STATION_SECRET")


async def main() -> None:
    async with ShipStationClient.scoped_client(
        v2_key=V2_API_KEY or "",
        v1_key=V1_API_KEY,
        v1_secret=V1_SECRET,
        version="v2",
    ) as connection:
        status, shipments = await ShipmentPortal.where(connection, page_size=10, page=1)
        print(status, shipments)


if __name__ == "__main__":
    asyncio.run(main())
~~~

### Start and close explicitly

Use `start()` and `close()` if you want to manage the lifecycle yourself.

~~~python
import asyncio
import os

from dotenv import load_dotenv

from AsyncShipStation import ShipStationClient, ShipmentPortal

load_dotenv()
V2_API_KEY: str | None = os.getenv("SHIP_STATION_V2")
V1_API_KEY: str | None = os.getenv("SHIP_STATION_V1")
V1_SECRET: str | None = os.getenv("SHIP_STATION_SECRET")


async def main() -> None:
    connection = await ShipStationClient.connect(
        v2_key=V2_API_KEY or "",
        v1_key=V1_API_KEY,
        v1_secret=V1_SECRET,
    )

    await ShipStationClient.start(connection=connection, version="v2")
    try:
        status, shipments = await ShipmentPortal.where(connection, page_size=10, page=1)
        print(status, shipments)
    finally:
        await ShipStationClient.close(connection=connection, version="v2")


if __name__ == "__main__":
    asyncio.run(main())
~~~

## Concurrent Requests

A single connection is meant to be shared across concurrent requests.

~~~python
import asyncio

from AsyncShipStation import (
    BatchPortal,
    LabelPortal,
    ShipmentPortal,
    ShipStationClient,
)


async def main() -> None:
    connection = await ShipStationClient.connect(
        v2_key="your_v2_api_key",
        v1_key="your_v1_api_key",
        v1_secret="your_v1_secret",
    )

    async with ShipStationClient.scoped_client(connection=connection, version="v2"):
        results = await asyncio.gather(
            ShipmentPortal.where(connection, page_size=10, page=1),
            BatchPortal.where(connection, page_size=10, page=1),
            LabelPortal.where(connection, page_size=10, page=1),
        )

    for status, data in results:
        if status in (200, 201, 207):
            print(f"Success :: {data}")
        else:
            print(f"Error :: {data}")


if __name__ == "__main__":
    asyncio.run(main())
~~~

## Connection Lookup

If you need to retrieve a connection from the pool later, use `connection.uid`.

~~~python
import asyncio

from AsyncShipStation import ShipStationClient, ShipmentPortal


async def main() -> None:
    connection = await ShipStationClient.connect(
        v2_key="your_v2_api_key",
        v1_key="your_v1_api_key",
        v1_secret="your_v1_secret",
    )

    async with ShipStationClient.scoped_client(
        uid=connection.uid,
        version="v2",
    ) as scoped_connection:
        status, shipments = await ShipmentPortal.where(
            scoped_connection,
            page_size=10,
            page=1,
        )
        print(status, shipments)


if __name__ == "__main__":
    asyncio.run(main())
~~~

## Errors

Portal methods return `(status, body)`. On failure `body` is an `ErrorResponse` with the response's real status:

- ShipStation v2 error bodies (`{"errors": [...]}`) are passed through unchanged.
- Any other error body, such as a v1 `{"Message": ...}` body, a non-JSON body or an empty one, is wrapped in an `ErrorResponse` that keeps its status (401, 404, 502, ...).
- Calling a version the connection has no credentials for fails locally with status 400.
- Transport failures (timeouts, refused connections) and undecodable success bodies come back as 500.

The one exception is rate limiting: `OrderPortal` raises `RateLimitError` on HTTP 429 (see below).

Logging is off by default. `ShipStationClient.debug_on()` enables it for every portal, and `debug_off()` disables it again.

## Rate Limiting

Accounts that send too many requests in quick succession will receive a `429 Too Many Requests` response. The v1 API allows 40 requests per minute per key and secret, and its 429s carry an `X-Rate-Limit-Reset` header (seconds until the window resets); v2 sends `Retry-After`.

The client does not retry 429s. `OrderPortal` raises `RateLimitError`, whose `retry_after` holds the wait in seconds (or `None` if neither header was readable), so you can back off:

~~~python
from AsyncShipStation import OrderPortal, RateLimitError

try:
    status, orders = await OrderPortal.where(connection, orderStatus="awaiting_shipment")
except RateLimitError as exc:
    await asyncio.sleep(exc.retry_after or 60)
~~~

Every other portal returns `(429, ErrorResponse)`. `ConnectionConfig.retries` only retries failed connection attempts, and `ConnectionConfig.timeout` defaults to 60 seconds.

ShipStation bulk operation endpoints count as a single request.

## TODO
