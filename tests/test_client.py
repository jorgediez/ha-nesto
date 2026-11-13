"""The client against a pretend panel on 127.0.0.1."""

from __future__ import annotations

import asyncio
import json
import secrets

import pytest

from custom_components.nesto import client as c, protocol as p

from .fake_panel import PANEL_ID, FakePanel, Refuse

pytestmark = pytest.mark.usefixtures("socket_enabled")

KEY = bytes(range(32))


@pytest.fixture
async def panel():
    panel = FakePanel()
    await panel.start()
    yield panel
    await panel.stop()


@pytest.fixture
async def paired(panel):
    panel.key = KEY
    return panel


async def open_session(panel: FakePanel, **kwargs) -> c.NestoClient:
    client = c.NestoClient("127.0.0.1", panel.port, timeout=2, **kwargs)
    await client.connect()
    await client.authenticate(KEY)
    return client


# Connecting ------------------------------------------------------------------


async def test_connect_reads_hello(panel):
    client = c.NestoClient("127.0.0.1", panel.port)
    hello = await client.connect()
    assert hello.panel_id == PANEL_ID
    assert hello.fw == "0.1.0"
    assert hello.model == "nest-gen2"
    assert hello.paired is False
    assert hello.pairing_open is True
    assert hello.caps == {"beeper": True}
    assert client.hello is hello
    assert not client.authenticated
    await client.close()


async def test_nothing_listening():
    client = c.NestoClient("127.0.0.1", 1, timeout=2)
    with pytest.raises(c.CannotConnect):
        await client.connect()


async def test_hello_before_connect():
    with pytest.raises(c.NestoError):
        _ = c.NestoClient("127.0.0.1").hello


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ({"proto": 2}, c.UnsupportedProtocol),
        ({"insecure": True}, c.InsecurePanel),
        ({"panel_id": "not a panel id"}, c.InvalidResponse),
        ({"nonce": "short"}, c.InvalidResponse),
        ({"t": "welcome"}, c.InvalidResponse),
    ],
)
async def test_a_bad_hello_is_refused(panel, extra, error):
    panel.hello_extra = extra
    client = c.NestoClient("127.0.0.1", panel.port, timeout=2)
    with pytest.raises(error):
        await client.connect()


async def test_an_unsupported_protocol_names_its_version(panel):
    panel.hello_extra = {"proto": 2}
    with pytest.raises(c.UnsupportedProtocol) as caught:
        await c.NestoClient("127.0.0.1", panel.port).connect()
    assert caught.value.version == 2


async def test_a_silent_panel_times_out():
    async def silent(reader, writer):
        await reader.read()

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(c.CannotConnect):
            await c.NestoClient("127.0.0.1", port, timeout=0.2).connect()
    finally:
        server.close()
        await server.wait_closed()


# Pairing ---------------------------------------------------------------------


async def test_pairing(panel):
    client = c.NestoClient("127.0.0.1", panel.port, timeout=2)
    await client.connect()
    code = await client.begin_pairing()
    assert code == panel.code
    assert len(code) == 6
    assert code.isdigit()

    key = await client.confirm_pairing()
    assert key == panel.key

    # The panel closes after pairing; the key opens the next connection.
    client = c.NestoClient("127.0.0.1", panel.port, timeout=2)
    assert (await client.connect()).paired
    await client.authenticate(key)
    assert client.authenticated
    await client.close()


async def test_pairing_closed(paired):
    client = c.NestoClient("127.0.0.1", paired.port, timeout=2)
    await client.connect()
    with pytest.raises(c.PanelError) as caught:
        await client.begin_pairing()
    assert caught.value.code == "pairing_closed"


async def test_pairing_cancelled(panel):
    client = c.NestoClient("127.0.0.1", panel.port, timeout=2)
    await client.connect()
    await client.begin_pairing()
    await client.cancel_pairing()
    assert panel.key is None


async def test_confirm_without_begin(panel):
    client = c.NestoClient("127.0.0.1", panel.port, timeout=2)
    await client.connect()
    with pytest.raises(c.NestoError):
        await client.confirm_pairing()
    await client.close()


async def test_a_low_order_point_is_refused(panel):
    panel.zero_point = True
    client = c.NestoClient("127.0.0.1", panel.port, timeout=2)
    await client.connect()
    with pytest.raises(c.InvalidResponse):
        await client.begin_pairing()


async def test_a_wrong_confirmation_from_the_panel(panel):
    panel.wrong_confirm_mac = True
    client = c.NestoClient("127.0.0.1", panel.port, timeout=2)
    await client.connect()
    await client.begin_pairing()
    with pytest.raises(c.AuthenticationFailed):
        await client.confirm_pairing()


async def test_the_panel_rejects_the_confirmation(panel):
    panel.reject_confirmation = True
    client = c.NestoClient("127.0.0.1", panel.port, timeout=2)
    await client.connect()
    await client.begin_pairing()
    with pytest.raises(c.AuthenticationFailed):
        await client.confirm_pairing()


# Authenticating --------------------------------------------------------------


async def test_authenticate(paired):
    client = await open_session(paired)
    assert client.authenticated
    assert paired.sessions == 1
    await client.close()
    assert not client.authenticated


async def test_the_panel_doesnt_know_the_key(paired):
    client = c.NestoClient("127.0.0.1", paired.port, timeout=2)
    await client.connect()
    with pytest.raises(c.AuthenticationFailed):
        await client.authenticate(secrets.token_bytes(32))
    assert not client.authenticated


async def test_the_panel_proves_the_wrong_key(paired):
    paired.wrong_auth_mac = True
    client = c.NestoClient("127.0.0.1", paired.port, timeout=2)
    await client.connect()
    with pytest.raises(c.AuthenticationFailed):
        await client.authenticate(KEY)
    assert not client.authenticated


async def test_send_before_the_session(paired):
    client = c.NestoClient("127.0.0.1", paired.port, timeout=2)
    await client.connect()
    with pytest.raises(c.CannotConnect):
        await client.send({"t": "time"})
    with pytest.raises(c.CannotConnect):
        await client.request({"t": "info"})
    await client.close()


# The session -----------------------------------------------------------------


async def test_events_and_requests(paired):
    paired.handlers["info"] = lambda message: {"uptime_s": 42}
    client = await open_session(paired)

    await client.send({"t": "display", "state": "off", "note": "ñ"})
    assert (await paired.wait_for("display"))["note"] == "ñ"

    result = await client.request({"t": "info"})
    assert result["uptime_s"] == 42
    await client.close()


async def test_a_refused_request(paired):
    def refuse(message):
        raise Refuse("unsupported")

    paired.handlers["reboot"] = refuse
    client = await open_session(paired)
    with pytest.raises(c.PanelError) as caught:
        await client.request({"t": "reboot"})
    assert caught.value.code == "unsupported"
    await client.close()


async def test_a_request_without_an_answer(paired):
    paired.unanswered.add("identify")
    client = await open_session(paired)
    with pytest.raises(c.CannotConnect):
        await client.request({"t": "identify"}, timeout=0.2)
    await client.close()


async def test_messages_from_the_panel(paired):
    received = []
    client = await open_session(paired, on_message=received.append)
    await paired.send({"t": "ready"})
    await paired.send({"t": "pong", "seq": 1})  # pongs aren't passed on
    await paired.send({"t": "result", "re": 999, "ok": True})  # nor stray results
    await paired.send({"t": "input", "gesture": "double", "context": "standby"})
    await asyncio.sleep(0.1)
    assert [m["t"] for m in received] == ["ready", "input"]
    await client.close()


async def test_the_panels_pings_are_answered(paired):
    client = await open_session(paired)
    await paired.send({"t": "ping", "seq": 7})
    assert (await paired.wait_for("pong"))["seq"] == 7
    await client.close()


async def test_answering_the_panels_requests(paired):
    intents = []
    client = await open_session(paired, on_message=intents.append)
    await paired.send({"t": "intent", "id": 5, "key": "l1", "action": "toggle"})
    await asyncio.sleep(0.1)
    assert intents[0]["id"] == 5

    await client.respond(5)
    assert await paired.wait_for("result") == {"t": "result", "re": 5, "ok": True}
    await client.respond(6, "unknown_key", "No item l9")
    assert (await paired.wait_for("result"))["error"] == {
        "code": "unknown_key",
        "message": "No item l9",
    }
    await client.close()


async def test_a_message_too_large(paired):
    client = await open_session(paired)
    with pytest.raises(c.NestoError):
        await client.send({"t": "notify", "message": "x" * p.MAX_FRAME})
    await client.close()


# Losing the connection -------------------------------------------------------


async def test_the_panel_closes_the_connection(paired):
    closed = asyncio.get_running_loop().create_future()
    client = await open_session(paired, on_close=closed.set_result)
    await paired.drop()
    reason = await asyncio.wait_for(closed, 2)
    assert isinstance(reason, c.CannotConnect)
    assert not client.authenticated
    with pytest.raises(c.CannotConnect):
        await client.send({"t": "time"})


async def test_open_requests_fail_when_the_connection_breaks(paired):
    paired.unanswered.add("identify")
    client = await open_session(paired)
    request = asyncio.create_task(client.request({"t": "identify"}, timeout=5))
    await paired.wait_for("identify")
    await paired.drop()
    with pytest.raises(c.NestoError):
        await request


async def test_garbage_from_the_panel(paired):
    closed = asyncio.get_running_loop().create_future()
    client = await open_session(paired, on_close=closed.set_result)
    await paired.send_raw(b"\x00\x00\x00\x20" + bytes(32))
    assert isinstance(await asyncio.wait_for(closed, 2), c.InvalidResponse)
    assert not client.authenticated


async def test_a_frame_too_large_from_the_panel(paired):
    closed = asyncio.get_running_loop().create_future()
    await open_session(paired, on_close=closed.set_result)
    await paired.send_raw((p.MAX_FRAME + 1).to_bytes(4, "big"))
    assert isinstance(await asyncio.wait_for(closed, 2), c.InvalidResponse)


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'{"no": "type"}'])
async def test_messages_that_arent_messages(paired, body):
    closed = asyncio.get_running_loop().create_future()
    client = await open_session(paired, on_close=closed.set_result)
    key, counter = paired._out
    await paired.send_raw(p.seal(key, counter, body))
    assert isinstance(await asyncio.wait_for(closed, 2), c.InvalidResponse)
    assert not client.authenticated


async def test_closing_ourselves_isnt_reported(paired):
    closed = []
    client = await open_session(paired, on_close=closed.append)
    await client.close()
    await client.close()  # twice is harmless
    await asyncio.sleep(0.1)
    assert closed == []


# Keeping it alive ------------------------------------------------------------


async def test_pings_keep_the_connection_alive(paired, monkeypatch):
    monkeypatch.setattr(c, "PING_INTERVAL", 0.05)
    monkeypatch.setattr(c, "IDLE_TIMEOUT", 0.3)
    closed = []
    client = await open_session(paired, on_close=closed.append)
    ping = await paired.wait_for("ping")
    assert ping["seq"] == 1
    await asyncio.sleep(0.5)
    assert client.authenticated
    assert closed == []
    await client.close()


async def test_a_quiet_panel_is_dropped(paired, monkeypatch):
    monkeypatch.setattr(c, "PING_INTERVAL", 0.05)
    monkeypatch.setattr(c, "IDLE_TIMEOUT", 0.2)
    paired.answer_pings = False
    closed = asyncio.get_running_loop().create_future()
    client = await open_session(paired, on_close=closed.set_result)
    reason = await asyncio.wait_for(closed, 2)
    assert isinstance(reason, c.CannotConnect)
    assert "nothing from the panel" in str(reason)
    assert not client.authenticated


# Helpers -----------------------------------------------------------------------


def test_base64():
    assert c.unb64(c.b64(b"abc")) == b"abc"
    with pytest.raises(c.InvalidResponse):
        c.unb64(42)
    with pytest.raises(c.InvalidResponse):
        c.unb64("!!!")
    with pytest.raises(c.InvalidResponse):
        c.unb64(c.b64(b"abc"), 4)


def test_an_error_result_without_details():
    with pytest.raises(c.PanelError) as caught:
        c._checked({"t": "result", "re": 1, "ok": False})
    assert caught.value.code == "internal"
    assert json.dumps(str(caught.value))
