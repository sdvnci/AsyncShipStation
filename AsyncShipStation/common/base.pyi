from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from threading import Lock
from typing import Any, ClassVar, Final, Literal, TypeVar

from httpx import AsyncClient, Response
from pydantic import EmailStr, HttpUrl, SecretStr

from ._types import ErrorResponse

LOGGER: Logger
VERSION: Final[str]
T = TypeVar("T")

HTTPMethods = Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]

class APIError(Exception):
    __slots__ = ("status_code", "details")
    status_code: int
    details: str | dict[str, object]

    def __init__(self, status: int, detail: str | dict[str, object]) -> None: ...
    def json(self) -> ErrorResponse: ...
    def __str__(self) -> str: ...
    @property
    def text(self) -> str: ...
    @property
    def content(self) -> bytes: ...

class RateLimitError(APIError):
    __slots__ = ("retry_after",)
    retry_after: float | None

    def __init__(
        self,
        status: int,
        detail: str | dict[str, object],
        retry_after: float | None,
    ) -> None: ...

class Loggable:
    __slots__ = ()
    _debug: ClassVar[bool]

    @classmethod
    def debug_on(cls: "type[Loggable]") -> bool: ...
    @classmethod
    def debug_off(cls: "type[Loggable]") -> bool: ...
    @classmethod
    def log(
        cls: "type[Loggable]", message: str, level: Literal[10, 20, 30, 40] = 20
    ) -> None: ...
    @classmethod
    def debug(cls: "type[Loggable]", message: str) -> None: ...
    @classmethod
    def info(cls: "type[Loggable]", message: str) -> None: ...
    @classmethod
    def warning(cls: "type[Loggable]", message: str) -> None: ...
    @classmethod
    def error(cls: "type[Loggable]", message: str) -> None: ...

@dataclass(slots=True, frozen=True)
class ConnectionConfig:
    version: Literal["v1", "v2", "both"] = "v2"
    timeout: int = 60
    max_connections: int = 20
    max_keepalive_connections: int = 10
    http2: bool = False
    retries: int = 4
    user_agent: str = ...
    v2_endpoint: str = "https://api.shipstation.com/v2"
    v2_mock_endpoint: str = "https://docs.shipstation.com/_mock/openapi/v2"
    v1_endpoint: str = "https://ssapi.shipstation.com"

    def __hash__(self) -> int: ...

class ShipStationConnection:
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
    ) -> None: ...
    def _build_client(self, base_url: str, headers: dict[str, str]) -> AsyncClient: ...
    async def start_v1(self) -> None: ...
    async def start_v2(self) -> None: ...
    async def start(self, version: Literal["v1", "v2", "both"] = "both") -> None: ...
    async def close(
        self, version: Literal["v1", "v2", "both"] = "both", force: bool = False
    ) -> None: ...
    async def v2_request(
        self,
        method: HTTPMethods,
        url: str,
        **kwargs: dict[str, str | int | bool | EmailStr | HttpUrl | None],
    ) -> Response | APIError: ...
    async def v1_request(
        self,
        method: HTTPMethods,
        url: str,
        **kwargs: dict[str, str | int | bool | EmailStr | HttpUrl | None],
    ) -> Response | APIError: ...
    async def request(
        self,
        method: HTTPMethods,
        url: str,
        version: Literal["v1", "v2"] = "v2",
        **kwargs: dict[str, str | int | bool | EmailStr | HttpUrl | None],
    ) -> Response | APIError: ...
    @property
    def v2_key(self) -> SecretStr | None: ...
    @property
    def v1_key(self) -> SecretStr | None: ...
    @property
    def v1_secret(self) -> SecretStr | None: ...
    @property
    def config(self) -> ConnectionConfig: ...
    @property
    def v2_endpoint(self) -> str: ...
    @property
    def v1_endpoint(self) -> str: ...
    @property
    def v2_ref_count(self) -> int: ...
    @property
    def v1_ref_count(self) -> int: ...
    @property
    def ref_count(self) -> int: ...
    @property
    def v2_active(self) -> bool: ...
    @property
    def v1_active(self) -> bool: ...
    @property
    def uid(self) -> int: ...
    @property
    def pool_key(self) -> int: ...
    async def increment_v2_ref(self) -> None: ...
    async def decrement_v2_ref(self) -> None: ...
    async def increment_v1_ref(self) -> None: ...
    async def decrement_v1_ref(self) -> None: ...
    def __eq__(self, other: object) -> bool: ...
    @staticmethod
    def hash(
        v2_key: str | None,
        v1_key: str | None,
        v1_secret: str | None,
        config: ConnectionConfig,
    ) -> int: ...
    def __hash__(self) -> int: ...

class ShipStationClient(Loggable):
    __slots__ = ()
    _physical_pool: ClassVar[dict[int, ShipStationConnection]]
    """
    The pool of all connection objects, where the key is the hashed credentials + config.
    """
    _virtual_pool: ClassVar[dict[int, int]]
    """
    A dict of "virtual" addresses which map to the "physical" hashes of each value.
    This gives a layer of abstraction between the uuid provided to the user, and the
    actual physical hash of an object.
    """
    _pool_lock: ClassVar[Lock]

    @staticmethod
    def _apply_identity_tag(
        payload: object,
        return_type: type[object],
    ) -> object: ...
    @classmethod
    def validate_response(
        cls: type["ShipStationClient"],
        res: Response | APIError,
        accepted_statuses: tuple[int, ...],
        return_type: type[T],
        identity: bool = False,
    ) -> tuple[int, ErrorResponse | T]: ...
    @staticmethod
    def _retry_after(res: Response) -> float | None: ...
    @staticmethod
    def parse_unknown_exception(
        exception: Exception,
    ) -> tuple[int, ErrorResponse]: ...
    @classmethod
    def _register(
        cls: type["ShipStationClient"], connection: ShipStationConnection
    ) -> ShipStationConnection: ...
    @classmethod
    async def evict_connection(cls: type["ShipStationClient"], uid: int) -> None: ...
    @classmethod
    async def get_connection(
        cls: type["ShipStationClient"],
        uid: int | None = None,
        v2_key: str | None = None,
        v1_key: str | None = None,
        v1_secret: str | None = None,
        config: ConnectionConfig | None = None,
    ) -> ShipStationConnection | None: ...
    @classmethod
    async def connect(
        cls: type["ShipStationClient"],
        uid: int | None = None,
        v2_key: str | None = None,
        v1_key: str | None = None,
        v1_secret: str | None = None,
        config: ConnectionConfig | None = None,
    ) -> ShipStationConnection: ...
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
    ) -> ShipStationConnection: ...
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
    ) -> None: ...
    @classmethod
    async def close_all(cls: type["ShipStationClient"]) -> None: ...
    @classmethod
    def scoped_client(
        cls: type["ShipStationClient"],
        v1_key: str | None = None,
        v1_secret: str | None = None,
        v2_key: str | None = None,
        connection: ShipStationConnection | None = None,
        uid: int | None = None,
        config: ConnectionConfig | None = None,
        version: Literal["v1", "v2", "both"] = "v2",
        mock: bool = False,
    ) -> AbstractAsyncContextManager[ShipStationConnection]: ...
    @classmethod
    async def request(
        cls: type["ShipStationClient"],
        method: HTTPMethods,
        url: str,
        version: Literal["v1", "v2"] = "v2",
        connection: ShipStationConnection | None = None,
        uid: int | None = None,
        **kwargs: dict[str, str | int | bool | EmailStr | HttpUrl | None],
    ) -> Response | APIError: ...

def write_json(fp: Path, data: dict[str, Any] | None) -> bool: ...
def read_json(fp: Path) -> dict[str, Any] | None: ...

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
