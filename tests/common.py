"""Panel data and a client mock shared by the integration tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from custom_components.nesto.client import Hello

HOST = "192.168.1.60"
PORT = 6170
PANEL_ID = "18b4300a1b2c"
TITLE = "Nesto Panel 0a1b2c"
KEY = bytes(range(32))
CODE = "123456"


def make_hello(**overrides: Any) -> Hello:
    """Return the hello of a paired Nest Gen 2 panel."""
    fields: dict[str, Any] = {
        "panel_id": PANEL_ID,
        "proto": 1,
        "fw": "0.1.0",
        "model": "nest-gen2",
        "paired": True,
        "pairing_open": False,
        "nonce": bytes(16),
        "caps": {"beeper": True, "input": ["ring", "press"]},
    }
    fields.update(overrides)
    return Hello(**fields)


class ClientFactory:
    """Stands in for NestoClient: each connection is a new mock.

    Tests set `hello`, and `connect_error` or `authenticate_error`, before the
    code under test connects; `clients` holds every mock made, newest last.
    """

    def __init__(self) -> None:
        self.hello = make_hello()
        self.connect_error: Exception | None = None
        self.authenticate_error: Exception | None = None
        self.begin_error: Exception | None = None
        self.confirm_error: Exception | None = None
        self.request_error: Exception | None = None
        self.codes = [CODE]
        self.clients: list[MagicMock] = []

    def __call__(self, host: str, port: int, **kwargs: Any) -> MagicMock:
        client = MagicMock()
        client.host, client.port = host, port
        client.on_message = kwargs.get("on_message")
        client.on_close = kwargs.get("on_close")
        client.authenticated = False
        client.hello = self.hello
        client.sent = []

        async def connect() -> Hello:
            if self.connect_error:
                raise self.connect_error
            return self.hello

        async def authenticate(key: bytes) -> None:
            if self.authenticate_error:
                raise self.authenticate_error
            client.key = key
            client.authenticated = True

        async def begin_pairing() -> str:
            if self.begin_error:
                raise self.begin_error
            return self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]

        async def confirm_pairing() -> bytes:
            if self.confirm_error:
                raise self.confirm_error
            return KEY

        async def send(message: dict[str, Any]) -> None:
            client.sent.append(message)

        async def request(message: dict[str, Any], _seconds: float | None = None):
            if self.request_error:
                raise self.request_error
            client.sent.append(message)
            return {"t": "result", "ok": True}

        async def close() -> None:
            client.authenticated = False

        client.connect = AsyncMock(side_effect=connect)
        client.authenticate = AsyncMock(side_effect=authenticate)
        client.begin_pairing = AsyncMock(side_effect=begin_pairing)
        client.confirm_pairing = AsyncMock(side_effect=confirm_pairing)
        client.cancel_pairing = AsyncMock(side_effect=close)
        client.send = AsyncMock(side_effect=send)
        client.request = AsyncMock(side_effect=request)
        client.respond = AsyncMock()
        client.close = AsyncMock(side_effect=close)
        self.clients.append(client)
        return client

    @property
    def last(self) -> MagicMock:
        return self.clients[-1]

    def drop(self, reason: Exception | None = None) -> None:
        """Make the newest connection break, as the client reports it."""
        client = self.last
        client.authenticated = False
        client.on_close(reason)
