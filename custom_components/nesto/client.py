"""One connection to a Nesto panel, over asyncio.

The panel listens on TCP and Home Assistant connects (PROTOCOL.md section 3).
A connection starts with the panel's `hello`, then either pairs (section 6),
after which the panel closes it, or authenticates (section 7) and carries on
encrypted. From then on a reader task delivers what the panel sends, answers
its pings, and matches results to requests, while a ping task keeps the
connection alive and closes it when the panel goes quiet (section 18).

No Home Assistant imports: the scripts use it as is.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import json
import logging
import re
import secrets
import time
from typing import Any

from . import protocol as p

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 6170
PROTOCOL_VERSION = 1
CONNECT_TIMEOUT = 10.0
PING_INTERVAL = 20.0
# Either side closes an authenticated connection after this long without
# receiving a frame (section 18).
IDLE_TIMEOUT = 60.0
MAX_OPEN_REQUESTS = 16  # per side (section 21)

_PANEL_ID = re.compile(r"[0-9A-Za-z._-]{1,64}")

type MessageHandler = Callable[[dict[str, Any]], None]
type CloseHandler = Callable[[Exception | None], None]


class NestoError(Exception):
    """Something went wrong talking to the panel."""


class CannotConnect(NestoError):
    """The panel can't be reached, or the connection broke."""


class InvalidResponse(NestoError):
    """The panel sent something that breaks the protocol."""


class UnsupportedProtocol(NestoError):
    """The panel speaks a protocol version this client doesn't."""

    def __init__(self, version: object) -> None:
        """Initialise with the version the panel announced."""
        super().__init__(f"protocol version {version}")
        self.version = version


class InsecurePanel(NestoError):
    """The panel is a development build without pairing or encryption."""


class AuthenticationFailed(NestoError):
    """The panel doesn't hold the key this client has, or proved the wrong one."""


class PanelError(NestoError):
    """The panel answered a request with an error (section 20)."""

    def __init__(self, code: str, message: str = "") -> None:
        """Initialise with the panel's error code and message."""
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def b64(data: bytes) -> str:
    """Return bytes as base64 text."""
    return base64.b64encode(data).decode("ascii")


def unb64(text: object, size: int | None = None) -> bytes:
    """Return the bytes in base64 text, checking their length when given."""
    if not isinstance(text, str):
        raise InvalidResponse("expected base64 text")
    try:
        data = base64.b64decode(text, validate=True)
    except binascii.Error as err:
        raise InvalidResponse("invalid base64") from err
    if size is not None and len(data) != size:
        raise InvalidResponse(f"expected {size} bytes, got {len(data)}")
    return data


@dataclass(frozen=True)
class Hello:
    """What the panel says about itself when a connection opens (section 5)."""

    panel_id: str
    proto: int
    fw: str
    model: str
    paired: bool
    pairing_open: bool
    nonce: bytes
    caps: dict[str, Any] = field(default_factory=dict)
    insecure: bool = False

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> Hello:
        """Return the hello in a message, or raise if it isn't a valid one."""
        if message.get("t") != "hello":
            raise InvalidResponse(f"expected hello, got {message.get('t')!r}")
        proto = message.get("proto")
        if proto != PROTOCOL_VERSION:
            raise UnsupportedProtocol(proto)
        panel_id = message.get("panel_id")
        if not isinstance(panel_id, str) or not _PANEL_ID.fullmatch(panel_id):
            raise InvalidResponse("invalid panel_id")
        caps = message.get("caps")
        return cls(
            panel_id=panel_id,
            proto=proto,
            fw=str(message.get("fw", "")),
            model=str(message.get("model", "")),
            paired=message.get("paired") is True,
            pairing_open=message.get("pairing_open") is True,
            nonce=unb64(message.get("nonce"), p.NONCE_SIZE),
            caps=caps if isinstance(caps, dict) else {},
            insecure=message.get("insecure") is True,
        )


@dataclass
class _Pairing:
    """A pairing between `pair_begin` and `pair_confirm`."""

    ha_pub: bytes
    panel_pub: bytes
    shared: bytes
    code: str


class NestoClient:
    """One connection to a panel.

    Use it once: connect, then pair or authenticate, and close. A broken
    connection calls `on_close` with the reason; closing it yourself doesn't.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        timeout: float = CONNECT_TIMEOUT,
        on_message: MessageHandler | None = None,
        on_close: CloseHandler | None = None,
    ) -> None:
        """Initialise; nothing is sent until `connect`."""
        self.host = host
        self.port = port
        self._timeout = timeout
        self._on_message = on_message
        self._on_close = on_close
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._hello: Hello | None = None
        self._pairing: _Pairing | None = None
        self._to_panel: tuple[bytes, int] | None = None  # key, counter
        self._from_panel: tuple[bytes, int] | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._slots = asyncio.Semaphore(MAX_OPEN_REQUESTS)
        self._tasks: list[asyncio.Task[None]] = []
        self._pongs: set[asyncio.Task[None]] = set()
        self._last_received = 0.0
        self._ping_seq = 0
        self._closed = False

    @property
    def hello(self) -> Hello:
        """Return the panel's hello; only after `connect`."""
        if self._hello is None:
            raise NestoError("not connected")
        return self._hello

    @property
    def authenticated(self) -> bool:
        """Return whether the session is authenticated and still open."""
        return self._to_panel is not None and not self._closed

    # Opening -----------------------------------------------------------------

    async def connect(self) -> Hello:
        """Open the connection and read the panel's hello."""
        try:
            async with asyncio.timeout(self._timeout):
                self._reader, self._writer = await asyncio.open_connection(
                    self.host, self.port
                )
                self._hello = Hello.from_message(await self._receive())
        except NestoError:
            await self.close()
            raise
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as err:
            await self.close()
            raise CannotConnect(
                f"{self.host}:{self.port}: {err or type(err).__name__}"
            ) from err
        if self._hello.insecure:
            await self.close()
            raise InsecurePanel("the panel runs without encryption")
        _LOGGER.debug(
            "Connected to panel %s (firmware %s) at %s:%s",
            self._hello.panel_id,
            self._hello.fw,
            self.host,
            self.port,
        )
        return self._hello

    # Pairing, section 6 ------------------------------------------------------

    async def begin_pairing(self) -> str:
        """Start pairing, and return the code the panel should be showing.

        The user compares it with the panel's: `confirm_pairing` if they
        match, `cancel_pairing` if not. The panel keeps its code on screen for
        two minutes.
        """
        private = secrets.token_bytes(p.KEY_SIZE)
        ha_pub = p.public_key(private)
        result = await self._plain_request({"t": "pair_begin", "pub": b64(ha_pub)})
        panel_pub = unb64(result.get("pub"), p.KEY_SIZE)
        try:
            shared = p.shared_secret(private, panel_pub)
        except p.ProtocolError as err:
            await self.close()
            raise InvalidResponse(str(err)) from err
        code = p.pairing_code(ha_pub, panel_pub, shared)
        self._pairing = _Pairing(ha_pub, panel_pub, shared, code)
        return code

    async def cancel_pairing(self) -> None:
        """Tell the panel the codes didn't match, and close."""
        self._pairing = None
        with contextlib.suppress(NestoError, OSError):
            await self._send_frame({"t": "pair_cancel"})
        await self.close()

    async def confirm_pairing(self) -> bytes:
        """Finish pairing, and return the key to keep.

        The panel closes the connection afterwards (section 6.2); authenticate
        on a new one.
        """
        pairing = self._pairing
        if pairing is None:
            raise NestoError("no pairing in progress")
        self._pairing = None
        key = p.pairing_key(pairing.shared, pairing.ha_pub, pairing.panel_pub)
        try:
            result = await self._plain_request(
                {"t": "pair_confirm", "mac": b64(p.confirm_mac(key, p.ROLE_HA))}
            )
        except PanelError as err:
            if err.code == "auth_failed":
                raise AuthenticationFailed("the panel rejected the pairing") from err
            raise
        finally:
            await self.close()
        if unb64(result.get("mac")) != p.confirm_mac(key, p.ROLE_PANEL):
            raise AuthenticationFailed("the panel's confirmation is wrong")
        return key

    # Authentication, section 7 -----------------------------------------------

    async def authenticate(self, key: bytes) -> None:
        """Authenticate with the pairing key, and start the encrypted session."""
        hello = self.hello
        ha_nonce = secrets.token_bytes(p.NONCE_SIZE)
        mac = p.auth_mac(key, p.ROLE_HA, hello.nonce, ha_nonce, hello.panel_id)
        try:
            result = await self._plain_request(
                {"t": "auth", "nonce": b64(ha_nonce), "mac": b64(mac)}
            )
        except PanelError as err:
            if err.code == "auth_failed":
                raise AuthenticationFailed("the panel doesn't know this key") from err
            raise
        expected = p.auth_mac(key, p.ROLE_PANEL, hello.nonce, ha_nonce, hello.panel_id)
        if unb64(result.get("mac")) != expected:
            await self.close()
            raise AuthenticationFailed("the panel's authentication is wrong")
        to_panel, from_panel = p.session_keys(key, hello.nonce, ha_nonce)
        self._to_panel = (to_panel, 0)
        self._from_panel = (from_panel, 0)
        self._last_received = time.monotonic()
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._read_loop(), name="nesto reader"),
            loop.create_task(self._ping_loop(), name="nesto ping"),
        ]

    # The session -------------------------------------------------------------

    async def send(self, message: dict[str, Any]) -> None:
        """Send an event: a message that gets no result."""
        self._require_session()
        await self._send_frame(message)

    async def request(
        self, message: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        """Send a request, and return its result; raise PanelError on an error."""
        self._require_session()
        async with self._slots:
            request_id = self._next_id
            self._next_id += 1
            future = asyncio.get_running_loop().create_future()
            self._pending[request_id] = future
            try:
                await self._send_frame({**message, "id": request_id})
                async with asyncio.timeout(timeout or self._timeout):
                    result = await future
            except TimeoutError as err:
                raise CannotConnect(
                    f"no answer to {message.get('t')!r} from the panel"
                ) from err
            finally:
                self._pending.pop(request_id, None)
        return _checked(result)

    async def respond(
        self,
        request_id: int,
        error: str | None = None,
        message: str = "",
        **fields: Any,
    ) -> None:
        """Answer one of the panel's requests: ok with fields, or an error code."""
        result: dict[str, Any] = {"t": "result", "re": request_id}
        if error is None:
            result.update(ok=True, **fields)
        else:
            result.update(ok=False, error={"code": error, "message": message})
        await self.send(result)

    async def close(self) -> None:
        """Close the connection. `on_close` isn't called for this."""
        self._closed = True
        await self._shut(None, notify=False)

    # Inside ------------------------------------------------------------------

    def _require_session(self) -> None:
        if not self.authenticated:
            raise CannotConnect("not connected to the panel")

    async def _send_frame(self, message: dict[str, Any]) -> None:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise CannotConnect("the connection is closed")
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        data = payload.encode()
        try:
            if self._to_panel is not None:
                key, counter = self._to_panel
                frame = p.seal(key, counter, data)
                self._to_panel = (key, counter + 1)
            else:
                frame = p.frame(data)
        except p.ProtocolError as err:
            raise NestoError(f"message too large: {err}") from err
        # The counter moves on with the write, in one step, so concurrent
        # senders can't put frames on the wire out of order.
        writer.write(frame)
        try:
            await writer.drain()
        except OSError as err:
            raise CannotConnect(str(err)) from err

    async def _receive(self) -> dict[str, Any]:
        reader = self._reader
        if reader is None:
            raise CannotConnect("not connected")
        header = await reader.readexactly(p.HEADER_SIZE)
        try:
            body = await reader.readexactly(p.frame_length(header))
            if self._from_panel is not None:
                key, counter = self._from_panel
                body = p.open_frame(key, counter, header + body)
                self._from_panel = (key, counter + 1)
        except p.ProtocolError as err:
            raise InvalidResponse(str(err)) from err
        try:
            message = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise InvalidResponse("a frame isn't JSON") from err
        if not isinstance(message, dict) or not isinstance(message.get("t"), str):
            raise InvalidResponse("a message without a type")
        return message

    async def _plain_request(
        self, message: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        """Send a request before the session, and wait for its result in turn."""
        request_id = self._next_id
        self._next_id += 1
        try:
            await self._send_frame({**message, "id": request_id})
            async with asyncio.timeout(timeout or self._timeout):
                while True:
                    reply = await self._receive()
                    if reply.get("t") == "result" and reply.get("re") == request_id:
                        return _checked(reply)
                    _LOGGER.debug("Ignoring %s before the session", reply.get("t"))
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as err:
            await self.close()
            raise CannotConnect(f"{message['t']}: {err or type(err).__name__}") from err
        except NestoError:
            # A refusal ends the connection too: the panel expects nothing more.
            await self.close()
            raise

    async def _read_loop(self) -> None:
        reason: Exception | None = None
        try:
            while True:
                message = await self._receive()
                self._last_received = time.monotonic()
                self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except asyncio.IncompleteReadError:
            reason = CannotConnect("the panel closed the connection")
        except (OSError, NestoError) as err:
            reason = err if isinstance(err, NestoError) else CannotConnect(str(err))
        await self._shut(reason, notify=True)

    def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message["t"]
        if kind == "result":
            request_id = message.get("re")
            future = (
                self._pending.get(request_id) if isinstance(request_id, int) else None
            )
            if future is not None and not future.done():
                future.set_result(message)
            return
        if kind == "ping":
            # Answered from the reader, so a busy consumer can't delay it.
            task = asyncio.get_running_loop().create_task(
                self._pong(message.get("seq")), name="nesto pong"
            )
            self._pongs.add(task)
            task.add_done_callback(self._pongs.discard)
            return
        if kind == "pong":
            return
        if self._on_message is not None:
            self._on_message(message)

    async def _pong(self, seq: object) -> None:
        with contextlib.suppress(NestoError):
            await self._send_frame({"t": "pong", "seq": seq})

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            if time.monotonic() - self._last_received > IDLE_TIMEOUT:
                await self._shut(
                    CannotConnect(f"nothing from the panel for {IDLE_TIMEOUT:.0f} s"),
                    notify=True,
                )
                return
            self._ping_seq += 1
            try:
                await self._send_frame({"t": "ping", "seq": self._ping_seq})
            except NestoError as err:
                await self._shut(err, notify=True)
                return

    async def _shut(self, reason: Exception | None, notify: bool) -> None:
        """Close everything once; tell `on_close` when the connection broke."""
        writer, self._writer = self._writer, None
        already_closed = writer is None and not self._tasks
        current = asyncio.current_task()
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            if task is not current:
                task.cancel()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(
                    reason or CannotConnect("the connection was closed")
                )
        self._pending.clear()
        self._to_panel = None
        self._from_panel = None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
        if notify and not already_closed and not self._closed:
            self._closed = True
            _LOGGER.debug("Connection to %s closed: %s", self.host, reason)
            if self._on_close is not None:
                self._on_close(reason)


def _checked(result: dict[str, Any]) -> dict[str, Any]:
    """Return a successful result, or raise the panel's error."""
    if result.get("ok") is True:
        return result
    error = result.get("error")
    if not isinstance(error, dict):
        error = {}
    raise PanelError(str(error.get("code", "internal")), str(error.get("message", "")))
