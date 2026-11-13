"""A pretend Nesto panel on 127.0.0.1, speaking the panel's side of the protocol.

Built on the same protocol module as the client, which the test vectors check
against the real firmware, so a conversation that works here works with a
panel. Tests can make it misbehave: refuse, prove the wrong key, send garbage.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import json
import secrets
import struct
from typing import Any

from custom_components.nesto import protocol as p
from custom_components.nesto.client import b64, unb64

PANEL_ID = "18b4300a1b2c"

type Handler = Callable[[dict[str, Any]], dict[str, Any]]


class Refuse(Exception):
    """Raised by a handler: the panel answers with this error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FakePanel:
    """Serves one connection at a time, as the panel does."""

    def __init__(self, key: bytes | None = None, panel_id: str = PANEL_ID) -> None:
        self.key = key
        self.panel_id = panel_id
        self.pairing_open: bool | None = None  # None: open while there's no key
        self.hello_extra: dict[str, Any] = {}
        self.code: str | None = None
        self.received: list[dict[str, Any]] = []
        self.handlers: dict[str, Handler] = {}
        self.unanswered: set[str] = set()  # request types it never answers
        self.answer_pings = True
        self.wrong_auth_mac = False
        self.wrong_confirm_mac = False
        self.reject_confirmation = False  # as if the keys differed
        self.zero_point = False  # answer pair_begin with a low-order point
        self.port = 0
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._out: tuple[bytes, int] | None = None
        self._in: tuple[bytes, int] | None = None
        self._arrived = asyncio.Condition()
        self.sessions = 0

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        await self.drop()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def drop(self) -> None:
        """Close the current connection, as a panel that restarts would."""
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def send(self, message: dict[str, Any]) -> None:
        """Send a message to Home Assistant over the current session."""
        assert self._writer is not None
        self._write(self._writer, message)
        await self._writer.drain()

    async def send_raw(self, data: bytes) -> None:
        assert self._writer is not None
        self._writer.write(data)
        await self._writer.drain()

    async def wait_for(self, kind: str, seconds: float = 5) -> dict[str, Any]:
        """Return the first received message of a type, waiting for it."""
        async with asyncio.timeout(seconds), self._arrived:
            while True:
                for message in self.received:
                    if message.get("t") == kind:
                        self.received.remove(message)
                        return message
                await self._arrived.wait()

    # The panel's side ----------------------------------------------------------

    def _write(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        data = json.dumps(message, separators=(",", ":")).encode()
        if self._out is not None:
            key, counter = self._out
            writer.write(p.seal(key, counter, data))
            self._out = (key, counter + 1)
        else:
            writer.write(p.frame(data))

    async def _read(self, reader: asyncio.StreamReader) -> dict[str, Any]:
        header = await reader.readexactly(4)
        (length,) = struct.unpack(">I", header)
        body = await reader.readexactly(length)
        if self._in is not None:
            key, counter = self._in
            body = p.open_frame(key, counter, header + body)
            self._in = (key, counter + 1)
        return json.loads(body)

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await self.drop()
        self._writer = writer
        self._out = self._in = None
        nonce = secrets.token_bytes(16)
        pairing: dict[str, bytes] = {}
        self._write(
            writer,
            {
                "t": "hello",
                "proto": 1,
                "panel_id": self.panel_id,
                "fw": "0.1.0",
                "model": "nest-gen2",
                "paired": self.key is not None,
                "pairing_open": self._pairing_open(),
                "nonce": b64(nonce),
                "caps": {"beeper": True},
                **self.hello_extra,
            },
        )
        try:
            while True:
                message = await self._read(reader)
                if self._in is not None:
                    await self._session(writer, message)
                elif not await self._handshake(writer, message, nonce, pairing):
                    break
        except asyncio.IncompleteReadError, ConnectionError, p.ProtocolError:
            pass
        finally:
            if self._writer is writer:
                self._writer = None
            writer.close()

    def _pairing_open(self) -> bool:
        return self.key is None if self.pairing_open is None else self.pairing_open

    async def _handshake(
        self,
        writer: asyncio.StreamWriter,
        message: dict[str, Any],
        nonce: bytes,
        pairing: dict[str, bytes],
    ) -> bool:
        """Handle a message before the session; return whether to go on."""
        kind, request_id = message.get("t"), message.get("id")

        def answer(**fields: Any) -> None:
            self._write(writer, {"t": "result", "re": request_id, "ok": True, **fields})

        def refuse(code: str) -> None:
            self._write(
                writer,
                {"t": "result", "re": request_id, "ok": False, "error": {"code": code}},
            )

        if kind == "pair_begin":
            if not self._pairing_open():
                refuse("pairing_closed")
                return True
            private = secrets.token_bytes(32)
            ha_pub = unb64(message["pub"], 32)
            panel_pub = bytes(32) if self.zero_point else p.public_key(private)
            if not self.zero_point:
                shared = p.shared_secret(private, ha_pub)
                self.code = p.pairing_code(ha_pub, panel_pub, shared)
                pairing.update(shared=shared, ha_pub=ha_pub, panel_pub=panel_pub)
            answer(pub=b64(panel_pub))
        elif kind == "pair_cancel":
            return False
        elif kind == "pair_confirm":
            key = p.pairing_key(
                pairing["shared"], pairing["ha_pub"], pairing["panel_pub"]
            )
            if self.reject_confirmation or unb64(message["mac"]) != p.confirm_mac(
                key, p.ROLE_HA
            ):
                refuse("auth_failed")
                return False
            self.key = key
            mac = p.confirm_mac(key, p.ROLE_PANEL)
            answer(mac=b64(bytes(64) if self.wrong_confirm_mac else mac))
            await writer.drain()
            return False  # the panel closes after pairing
        elif kind == "auth":
            ha_nonce = unb64(message["nonce"], 16)
            expected = (
                p.auth_mac(self.key, p.ROLE_HA, nonce, ha_nonce, self.panel_id)
                if self.key
                else None
            )
            if expected is None or unb64(message["mac"]) != expected:
                refuse("auth_failed")
                return False
            mac = p.auth_mac(self.key, p.ROLE_PANEL, nonce, ha_nonce, self.panel_id)
            answer(mac=b64(bytes(64) if self.wrong_auth_mac else mac))
            self._out, self._in = (
                (k, 0) for k in reversed(p.session_keys(self.key, nonce, ha_nonce))
            )
            self.sessions += 1
        else:
            refuse("bad_request")
        await writer.drain()
        return True

    async def _session(
        self, writer: asyncio.StreamWriter, message: dict[str, Any]
    ) -> None:
        kind, request_id = message.get("t"), message.get("id")
        if kind == "ping":
            if self.answer_pings:
                self._write(writer, {"t": "pong", "seq": message.get("seq")})
        elif (
            request_id is not None and kind != "result" and kind not in self.unanswered
        ):
            handler = self.handlers.get(kind, lambda _message: {})
            try:
                fields = handler(message)
                reply = {"t": "result", "re": request_id, "ok": True, **fields}
            except Refuse as err:
                reply = {
                    "t": "result",
                    "re": request_id,
                    "ok": False,
                    "error": {"code": err.code, "message": "refused"},
                }
            self._write(writer, reply)
        await writer.drain()
        async with self._arrived:
            self.received.append(message)
            self._arrived.notify_all()
