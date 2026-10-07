from base64 import b64encode
from contextlib import asynccontextmanager
from dataclasses import dataclass, fields
from hashlib import sha256
from json import JSONDecodeError, dump, dumps, load
from logging import DEBUG, ERROR, INFO, WARNING, Logger, getLogger
from pathlib import Path
from threading import Lock
from typing import Any, AsyncGenerator, ClassVar, Final, Literal, TypeVar, cast
from uuid import uuid4

from httpx import AsyncClient, AsyncHTTPTransport, Limits, Response
from pydantic import EmailStr, HttpUrl, SecretStr

from ._types import ErrorResponse

LOGGER: Logger = getLogger("AsyncShipStation")
VERSION: Final[str] = "0.2.2.0"
T = TypeVar("T")

HTTPMethods = Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


class APIError(Exception):
    """
    Raised or returned for local failures, such as calling a version the connection has
    no credentials for. Portals convert it to an ``ErrorResponse`` carrying ``status_code``.
    """

    __slots__ = ("status_code", "details")

    def __init__(self, status: int, detail: str | dict[str, object]) -> None:
        super().__init__(status, detail)
        self.status_code = status
        self.details = detail

    def json(self) -> ErrorResponse:
        return cast(
            ErrorResponse,
            {
                "request_id": None,
                "errors": [
                    {
                        "error_source": "ShipStation",
                        "error_type": "integrations",
                        "error_code": self.status_code,
                        "message": self.details,
                    }
                ],
            },
        )

    def __str__(self) -> str:
        outdict = {
            "status_code": self.status_code,
            "details": self.json(),
        }
        return dumps(outdict, indent=4, ensure_ascii=False)

    @property
    def text(self) -> str:
        return self.__str__()

    @property
    def content(self) -> bytes:
        return self.__str__().encode("utf-8")


class RateLimitError(APIError):
    """
    Raised by ``validate_response`` when ShipStation answers HTTP 429 Too Many Requests.

    ``retry_after`` is how many seconds to wait, taken from the ``Retry-After`` header
    or, when that is missing, v1's ``X-Rate-Limit-Reset``. It is None when neither is
    present. ``OrderPortal`` re-raises it; other portals return it as a
    ``(429, ErrorResponse)`` tuple.
    """

    __slots__ = ("retry_after",)

    def __init__(
        self,
        status: int,
        detail: str | dict[str, object],
        retry_after: float | None,
    ) -> None:
        super().__init__(status, detail)
        self.retry_after = retry_after


class Loggable:
    __slots__ = ()
    _debug: ClassVar[bool] = False

    @classmethod
    def debug_on(cls: "type[Loggable]") -> bool:
        """
        Enables logging for this class and all subclasses that have not set their own state.
        Returns:
            bool: The new debug state (True).
        """
        cls._debug = True
        return cls._debug

    @classmethod
    def debug_off(cls: "type[Loggable]") -> bool:
        """
        Disables logging for this class and all subclasses that have not set their own state.
        Returns:
            bool: The new debug state (False).
        """
        cls._debug = False
        return cls._debug

    @classmethod
    def log(
        cls: "type[Loggable]", message: str, level: Literal[10, 20, 30, 40] = INFO
    ) -> None:
        """
        Logs a message at the specified level if debugging is enabled.
        Args:
            message (str): The message to log.
            level (Literal[10, 20, 30, 40], optional): The logging level (DEBUG=10, INFO=20, WARNING=30, ERROR=40). Defaults to INFO.
        """
        if cls._debug:
            LOGGER.log(level, message)

    @classmethod
    def debug(cls: "type[Loggable]", message: str) -> None:
        cls.log(message, level=DEBUG)

    @classmethod
    def info(cls: "type[Loggable]", message: str) -> None:
        cls.log(message, level=INFO)

    @classmethod
    def warning(cls: "type[Loggable]", message: str) -> None:
        cls.log(message, level=WARNING)

    @classmethod
    def error(cls: "type[Loggable]", message: str) -> None:
        cls.log(message, level=ERROR)


@dataclass(slots=True, frozen=True)
class ConnectionConfig:
    """
    Transport settings for a connection. Part of the connection's identity: the same
    credentials with a different config are pooled as a separate connection.

    ``retries`` is the number of connect-level retries on the httpx transport. HTTP 429
    is never retried; it surfaces as a ``RateLimitError`` (see ``validate_response``).
    """

    version: Literal["v1", "v2", "both"] = "v2"
    timeout: int = 60
    max_connections: int = 20
    max_keepalive_connections: int = 10
    http2: bool = False
    retries: int = 4
    user_agent: str = f"asyncShipStation/{VERSION}"
    v2_endpoint: str = "https://api.shipstation.com/v2"
    v2_mock_endpoint: str = "https://docs.shipstation.com/_mock/openapi/v2"
    v1_endpoint: str = "https://ssapi.shipstation.com"

    def __hash__(self: "ConnectionConfig") -> int:
        raw = ":".join(f"{f.name}={getattr(self, f.name)}" for f in fields(self))
        digest = int.from_bytes(
            sha256(raw.encode("utf-8")).digest()[:8], "big", signed=True
        )
        return -2 if digest == -1 else digest


class ShipStationConnection:
    """
    One set of ShipStation credentials and the httpx clients that serve them.

    A ``v2_key`` enables the v2 API. A ``v1_key`` and ``v1_secret`` together enable the
    v1 API. Each version's client is reference counted: every ``start`` takes a
    reference, every ``close`` releases one, and the client is closed when its count
    reaches zero.
    """

    __slots__ = (
        "_v2_key",
        "_v1_key",
        "_v1_secret",
        "_v2_headers",
        "_v1_headers",
        "_v1_lock",
        "_v2_lock",
        "_v1_client",
        "_v2_client",
        "_v1_ref_count",
        "_v2_ref_count",
        "_v1_enabled",
        "_v2_enabled",
        "_pool_key",
        "_config",
        "_uid",
    )

    def __init__(
        self,
        v2_key: str | None = None,
        v1_key: str | None = None,
        v1_secret: str | None = None,
        config: ConnectionConfig | None = None,
    ) -> None:
        if bool(v1_key) != bool(v1_secret):
            raise ValueError("v1_key and v1_secret must be provided together.")
        if not v2_key and not v1_key:
            raise ValueError("Provide a v2_key, or a v1_key and v1_secret.")

        self._v2_key: SecretStr | None = SecretStr(v2_key) if v2_key else None
        self._v1_key: SecretStr | None = SecretStr(v1_key) if v1_key else None
        self._v1_secret: SecretStr | None = SecretStr(v1_secret) if v1_secret else None
        self._v1_enabled: bool = False
        self._v2_enabled: bool = False
        self._config: ConnectionConfig = config or ConnectionConfig()
        self._pool_key: int = self.hash(v2_key, v1_key, v1_secret, self._config)
        if self._v2_key:
            self._v2_headers: dict[str, str] = {
                "User-Agent": self._config.user_agent,
                "api-key": self._v2_key.get_secret_value(),
            }
            self._v2_enabled = True

        if v1_key and v1_secret:
            credentials = f"{v1_key}:{v1_secret}"
            encoded_credentials = b64encode(credentials.encode("utf-8")).decode("utf-8")
            self._v1_headers: dict[str, str] = {
                "User-Agent": self._config.user_agent,
                "Authorization": f"Basic {encoded_credentials}",
            }
            self._v1_enabled = True

        self._v1_lock: Lock = Lock()
        self._v2_lock: Lock = Lock()
        self._v1_client: AsyncClient | None = None
        self._v2_client: AsyncClient | None = None
        self._v1_ref_count: int = 0
        self._v2_ref_count: int = 0
        self._uid = uuid4().int

    def _build_client(
        self: "ShipStationConnection", base_url: str, headers: dict[str, str]
    ) -> AsyncClient:
        return AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=self._config.timeout,
            transport=AsyncHTTPTransport(
                retries=self._config.retries,
                http2=self._config.http2,
                limits=Limits(
                    max_connections=self._config.max_connections,
                    max_keepalive_connections=self._config.max_keepalive_connections,
                ),
            ),
        )

    async def start_v1(self: "ShipStationConnection") -> None:
        if not self._v1_enabled:
            raise APIError(400, "API v1 is not enabled for this connection.")
        with self._v1_lock:
            if self._v1_client is None or self._v1_client.is_closed:
                self._v1_client = self._build_client(
                    self._config.v1_endpoint, self._v1_headers
                )
            self._v1_ref_count += 1

    async def start_v2(self: "ShipStationConnection") -> None:
        if not self._v2_enabled:
            raise APIError(400, "API v2 is not enabled for this connection.")
        with self._v2_lock:
            if self._v2_client is None or self._v2_client.is_closed:
                self._v2_client = self._build_client(
                    self._config.v2_endpoint, self._v2_headers
                )
            self._v2_ref_count += 1

    async def start(
        self: "ShipStationConnection", version: Literal["v1", "v2", "both"] = "both"
    ) -> None:
        if version in ("v1", "both"):
            await self.start_v1()
        if version in ("v2", "both"):
            await self.start_v2()

    async def close(
        self: "ShipStationConnection",
        version: Literal["v1", "v2", "both"] = "both",
        force: bool = False,
    ) -> None:
        """
        Releases one reference per version, closing a client when none remain.
        ``force=True`` closes immediately and resets the count to zero.
        """
        stale: list[AsyncClient] = []
        if version in ("v1", "both") and self._v1_enabled:
            with self._v1_lock:
                self._v1_ref_count = max(0, self._v1_ref_count - 1)
                if self._v1_ref_count == 0 or force:
                    self._v1_ref_count = 0
                    if self._v1_client is not None:
                        stale.append(self._v1_client)
                    self._v1_client = None

        if version in ("v2", "both") and self._v2_enabled:
            with self._v2_lock:
                self._v2_ref_count = max(0, self._v2_ref_count - 1)
                if self._v2_ref_count == 0 or force:
                    self._v2_ref_count = 0
                    if self._v2_client is not None:
                        stale.append(self._v2_client)
                    self._v2_client = None

        for client in stale:
            await client.aclose()

    async def v2_request(
        self: "ShipStationConnection",
        method: HTTPMethods,
        url: str,
        **kwargs: dict[str, str | int | bool | EmailStr | HttpUrl | None],
    ) -> Response | APIError:
        """
        Sends a request on the v2 client. The request holds its own reference for its
        duration, so a connection that was never started still works (a client is opened
        and closed around the call), and concurrent requests never close the client out
        from under each other.
        """
        if not self._v2_enabled:
            return APIError(400, "API v2 is not enabled for this connection.")

        await self.start_v2()
        try:
            client = self._v2_client
            if client is None:
                return APIError(500, "HTTP client could not be initialized.")
            return await client.request(method, url, **kwargs)  # type: ignore[arg-type]
        finally:
            await self.close("v2")

    async def v1_request(
        self: "ShipStationConnection",
        method: HTTPMethods,
        url: str,
        **kwargs: dict[str, str | int | bool | EmailStr | HttpUrl | None],
    ) -> Response | APIError:
        """
        Sends a request on the v1 client, holding a reference for its duration
        (see ``v2_request``).
        """
        if not self._v1_enabled:
            return APIError(400, "API v1 is not enabled for this connection.")

        await self.start_v1()
        try:
            client = self._v1_client
            if client is None:
                return APIError(500, "HTTP client could not be initialized.")
            return await client.request(method, url, **kwargs)  # type: ignore[arg-type]
        finally:
            await self.close("v1")

    async def request(
        self: "ShipStationConnection",
        method: HTTPMethods,
        url: str,
        version: Literal["v1", "v2"] = "v2",
        **kwargs: dict[str, str | int | bool | EmailStr | HttpUrl | None],
    ) -> Response | APIError:
        if version == "v2":
            return await self.v2_request(method, url, **kwargs)
        elif version == "v1":
            return await self.v1_request(method, url, **kwargs)
        else:
            return APIError(400, f"Unsupported API version: {version}")

    @property
    def v2_key(self) -> SecretStr | None:
        return self._v2_key

    @property
    def v1_key(self) -> SecretStr | None:
        return self._v1_key

    @property
    def v1_secret(self) -> SecretStr | None:
        return self._v1_secret

    @property
    def config(self) -> ConnectionConfig:
        return self._config

    @property
    def v2_endpoint(self) -> str:
        return self._config.v2_endpoint

    @property
    def v1_endpoint(self) -> str:
        return self._config.v1_endpoint

    @property
    def v2_ref_count(self) -> int:
        return self._v2_ref_count

    @property
    def v1_ref_count(self) -> int:
        return self._v1_ref_count

    @property
    def ref_count(self) -> int:
        return self._v1_ref_count + self._v2_ref_count

    @property
    def v2_active(self) -> bool:
        return self._v2_client is not None and not self._v2_client.is_closed

    @property
    def v1_active(self) -> bool:
        return self._v1_client is not None and not self._v1_client.is_closed

    @property
    def uid(self) -> int:
        return self._uid

    @property
    def pool_key(self) -> int:
        return self._pool_key

    async def increment_v2_ref(self) -> None:
        with self._v2_lock:
            self._v2_ref_count += 1

    async def decrement_v2_ref(self) -> None:
        await self.close("v2")

    async def increment_v1_ref(self) -> None:
        with self._v1_lock:
            self._v1_ref_count += 1

    async def decrement_v1_ref(self) -> None:
        await self.close("v1")

    def __eq__(self: "ShipStationConnection", other: object) -> bool:
        if not isinstance(other, ShipStationConnection):
            return NotImplemented
        return (
            self._v2_key == other._v2_key
            and self._v1_key == other._v1_key
            and self._v1_secret == other._v1_secret
            and self._config == other._config
        )

    @staticmethod
    def hash(
        v2_key: str | None,
        v1_key: str | None,
        v1_secret: str | None,
        config: ConnectionConfig,
    ) -> int:
        raw = f"{v2_key or ''}:{v1_key or ''}:{v1_secret or ''}:{hash(config)}"
        digest = int.from_bytes(
            sha256(raw.encode("utf-8")).digest()[:8], "big", signed=True
        )
        return -2 if digest == -1 else digest

    def __hash__(self: "ShipStationConnection") -> int:
        return self._pool_key


class ShipStationClient(Loggable):
    __slots__ = ()

    _physical_pool: ClassVar[dict[int, ShipStationConnection]] = {}
    """
    The pool of all connection objects, where the key is the hashed credentials + config.
    """
    _virtual_pool: ClassVar[dict[int, int]] = {}
    """
    A dict of "virtual" addresses which map to the "physical" hashes of each value.
    This gives a layer of abstraction between the uuid provided to the user, and the
    actual physical hash of an object.
    """

    _pool_lock: ClassVar[Lock] = Lock()
    """
    Guards the pools. Critical sections never await, so a thread lock is safe here and,
    unlike an asyncio lock, is not tied to the event loop that first contended for it.
    """

    @staticmethod
    def _apply_identity_tag(
        payload: object,
        return_type: type[object],
    ) -> object:
        if isinstance(payload, dict):
            tagged = cast(dict[str, Any], payload)
            tagged["__kind__"] = return_type.__name__

            for shipment in tagged.get("shipments") or []:
                shipment["__kind__"] = "Shipment"
            for order in tagged.get("orders") or []:
                order["__kind__"] = "V1Order"

        return payload

    @classmethod
    def validate_response(
        cls: type["ShipStationClient"],
        res: Response | APIError,
        accepted_statuses: tuple[int, ...],
        return_type: type[T],
        identity: bool = False,
    ) -> tuple[int, ErrorResponse | T]:
        """
        Decodes a response into ``(status, payload)``. ShipStation error bodies
        (``{"errors": [...]}``) are passed through with their real status, and anything
        else that is not a successful JSON (or empty) body, such as a v1 error body, is
        wrapped in an ``ErrorResponse`` that keeps the response's status.

        The one exception raised is ``RateLimitError`` on HTTP 429, so a caller can back
        off for its ``retry_after``. Portals that catch it still return status 429.
        """
        if isinstance(res, APIError):
            return res.status_code, res.json()

        if res.status_code == 429:
            raise RateLimitError(
                429, res.text[:500] or res.reason_phrase, cls._retry_after(res)
            )

        payload: object = None
        if res.content:
            try:
                payload = res.json()
            except (JSONDecodeError, UnicodeDecodeError) as e:
                status = (
                    500 if res.status_code in accepted_statuses else res.status_code
                )
                return (
                    status,
                    APIError(
                        status,
                        f"Could not decode response body: {e}. Raw response: {res.text[:500]}",
                    ).json(),
                )

        if res.status_code not in accepted_statuses:
            if isinstance(payload, dict) and isinstance(payload.get("errors"), list):
                return res.status_code, cast(ErrorResponse, payload)

            detail = (
                payload
                if isinstance(payload, str)
                else (dumps(payload) if payload is not None else res.reason_phrase)
            )
            return res.status_code, APIError(res.status_code, detail).json()

        if identity:
            payload = cls._apply_identity_tag(payload, return_type)

        return res.status_code, cast(T, payload)

    @staticmethod
    def _retry_after(res: Response) -> float | None:
        """
        Seconds to wait before retrying, from ``Retry-After`` (v2) or
        ``X-Rate-Limit-Reset`` (v1). None when neither header holds a number.
        """
        for header in ("Retry-After", "X-Rate-Limit-Reset"):
            raw = res.headers.get(header)
            if raw is None:
                continue
            try:
                return float(raw)
            except ValueError:
                continue
        return None

    @staticmethod
    def parse_unknown_exception(
        exception: Exception,
    ) -> tuple[int, ErrorResponse]:
        """
        Parses an exception and returns a standardized error response.
        Args:
            exception (Exception): The exception to parse.
        Returns:
            tuple[int, ErrorResponse]: The APIError's status (500 for any other exception) and the error details.
        """
        if isinstance(exception, APIError):
            return exception.status_code, exception.json()

        return (
            500,
            cast(
                ErrorResponse,
                {
                    "request_id": None,
                    "errors": [
                        {
                            "error_source": "ShipStation",
                            "error_type": "integrations",
                            "error_code": "unknown",
                            "message": f"{type(exception).__name__}: {exception}",
                        }
                    ],
                },
            ),
        )

    @classmethod
    def _register(
        cls: type["ShipStationClient"], connection: ShipStationConnection
    ) -> ShipStationConnection:
        """
        Adds a connection to the pool unless one with the same credentials and config is
        already there, and returns whichever connection the pool holds for that key.
        """
        with cls._pool_lock:
            pooled = cls._physical_pool.setdefault(connection.pool_key, connection)
            cls._virtual_pool[pooled.uid] = pooled.pool_key

        if pooled is connection:
            cls.info(f"_register:::Connection with uid {connection.uid} added to pool")
        return pooled

    @classmethod
    async def evict_connection(cls: type["ShipStationClient"], uid: int) -> None:
        with cls._pool_lock:
            physical_addr = cls._virtual_pool.pop(uid, None)
            if physical_addr is None:
                cls.info(
                    f"evict_connection:::Could not find any connection with uuid {uid}"
                )
                return

            pooled = cls._physical_pool.get(physical_addr)
            if pooled is not None and pooled.uid == uid:
                del cls._physical_pool[physical_addr]

        cls.info(f"evict_connection:::Connection with uuid {uid} evicted from pool")

    @classmethod
    async def get_connection(
        cls: type["ShipStationClient"],
        uid: int | None = None,
        v2_key: str | None = None,
        v1_key: str | None = None,
        v1_secret: str | None = None,
        config: ConnectionConfig | None = None,
    ) -> ShipStationConnection | None:
        """
        Looks up a pooled connection by ``uid``, or by credentials + config
        (the default config when none is given).
        """
        with cls._pool_lock:
            if uid is not None:
                physical = cls._virtual_pool.get(uid, None)
                if physical is None:
                    cls.info(
                        f"get_connection:::No connection object with uuid {uid} found."
                    )
                    return None
                return cls._physical_pool.get(physical, None)

            if v2_key or (v1_key and v1_secret):
                physical = ShipStationConnection.hash(
                    v2_key, v1_key, v1_secret, config or ConnectionConfig()
                )
                return cls._physical_pool.get(physical, None)

            return None

    @classmethod
    async def connect(
        cls: type["ShipStationClient"],
        uid: int | None = None,
        v2_key: str | None = None,
        v1_key: str | None = None,
        v1_secret: str | None = None,
        config: ConnectionConfig | None = None,
    ) -> ShipStationConnection:
        """
        Returns the pooled connection for these credentials, creating it if needed.
        Connecting twice with the same credentials and config returns the same object.
        """
        found = await cls.get_connection(uid, v2_key, v1_key, v1_secret, config)
        if found is not None:
            return found

        if uid is not None and not (v2_key or (v1_key and v1_secret)):
            raise ValueError(
                f"No pooled connection with uid {uid}, and no credentials to create one."
            )

        return cls._register(ShipStationConnection(v2_key, v1_key, v1_secret, config))

    @classmethod
    async def start(
        cls: type["ShipStationClient"],
        uid: int | None = None,
        v1_key: str | None = None,
        v1_secret: str | None = None,
        v2_key: str | None = None,
        connection: ShipStationConnection | None = None,
        config: ConnectionConfig | None = None,
        version: Literal["v1", "v2", "both"] = "both",
    ) -> ShipStationConnection:

        if version not in ("v1", "v2", "both"):
            raise ValueError(f"Unsupported version: {version}")

        if connection is None:
            connection = await cls.connect(
                uid=uid,
                v2_key=v2_key,
                v1_key=v1_key,
                v1_secret=v1_secret,
                config=config,
            )
        else:
            cls._register(connection)

        await connection.start(version)
        return connection

    @classmethod
    async def close(
        cls: type["ShipStationClient"],
        v1_key: str | None = None,
        v1_secret: str | None = None,
        v2_key: str | None = None,
        connection: ShipStationConnection | None = None,
        uid: int | None = None,
        config: ConnectionConfig | None = None,
        version: Literal["v1", "v2", "both"] = "v2",
        force: bool = False,
    ) -> None:
        """
        Releases one reference on a connection.

        The client is only closed, and the connection evicted from the pool, when no
        references remain. Pass ``force=True`` to close unconditionally.
        """
        if version not in ("v1", "v2", "both"):
            raise ValueError(f"Unsupported version: {version}")

        conn = (
            connection
            if connection is not None
            else await cls.get_connection(uid, v2_key, v1_key, v1_secret, config)
        )

        if conn is None:
            raise ValueError(
                "No connection found to close. Provide a valid connection, uid, or credentials."
            )

        await conn.close(version, force=force)
        if conn.ref_count == 0:
            await cls.evict_connection(conn.uid)

    @classmethod
    async def close_all(cls: type["ShipStationClient"]) -> None:
        """
        Force-closes every pooled connection and empties the pool. Meant for shutdown.
        """
        with cls._pool_lock:
            connections = list(cls._physical_pool.values())
            cls._physical_pool.clear()
            cls._virtual_pool.clear()

        for conn in connections:
            await conn.close("both", force=True)

    @classmethod
    @asynccontextmanager
    async def scoped_client(
        cls: type["ShipStationClient"],
        v1_key: str | None = None,
        v1_secret: str | None = None,
        v2_key: str | None = None,
        connection: ShipStationConnection | None = None,
        uid: int | None = None,
        config: ConnectionConfig | None = None,
        version: Literal["v1", "v2", "both"] = "v2",
        mock: bool = False,
    ) -> AsyncGenerator[ShipStationConnection, None]:
        connection = await cls.start(
            uid=uid,
            v1_key=v1_key,
            v1_secret=v1_secret,
            v2_key=v2_key,
            connection=connection,
            version=version,
            config=config,
        )

        try:
            yield connection
        finally:
            await cls.close(connection=connection, version=version)

    @classmethod
    async def request(
        cls: type["ShipStationClient"],
        method: HTTPMethods,
        url: str,
        version: Literal["v1", "v2"] = "v2",
        connection: ShipStationConnection | None = None,
        uid: int | None = None,
        **kwargs: dict[str, str | int | bool | EmailStr | HttpUrl | None],
    ) -> Response | APIError:
        if connection is None:
            if uid is None:
                raise ValueError("Either a connection or uid must be provided.")
            connection = await cls.get_connection(uid=uid)
            if not connection:
                raise ValueError(
                    "No connection found for the provided uid. A connection must be started before making requests."
                )

        return await connection.request(method, url, version=version, **kwargs)


def write_json(fp: Path, data: dict[str, Any] | None) -> bool:
    """
    Writes a dictionary to a JSON file at the specified path.
    Args:
        fp (Path): The file path where the JSON data should be written.
        data (dict[str, Any] | None): The data to write to the JSON file. If None, no action is taken.
    Returns:
        bool: True if the data was written successfully, False otherwise.
    """
    if not data:
        LOGGER.warning(f"write_json:::No data to write to {fp}")
        return False

    try:
        with open(fp, "w") as f:
            dump(data, f, indent=4, ensure_ascii=False)
            LOGGER.info(f"write_json:::{fp} written to successfully")
            return True
    except (IOError, OSError) as err:
        LOGGER.error(f"write_json:::Failed to write data {err} to file {fp}")
        return False


def read_json(fp: Path) -> dict[str, Any] | None:
    """
    Reads a JSON file from the specified path and returns its content as a dictionary.
    Args:
        fp (Path): The file path from which to read the JSON data.
    Returns:
        dict[str, Any] | None: The data read from the JSON file as a dictionary, or None if the file does not exist or an error occurs.
    """
    if not fp.exists():
        LOGGER.warning(f"read_json:::File {fp} does not exist.")
        return None

    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = load(f)
            LOGGER.info(f"read_json:::{fp} read successfully")
            return cast(dict[str, Any], data)
    except (IOError, OSError, JSONDecodeError) as err:
        LOGGER.error(f"read_json:::Failed to read data from {fp} with error: {err}")
        return None


__all__ = (
    "LOGGER",
    "VERSION",
    "HTTPMethods",
    "APIError",
    "RateLimitError",
    "Loggable",
    "ConnectionConfig",
    "ShipStationConnection",
    "ShipStationClient",
    "write_json",
    "read_json",
)
