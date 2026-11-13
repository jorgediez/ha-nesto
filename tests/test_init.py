"""Setting up, staying connected, synchronising, and removing a panel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.nesto import coordinator as coordinator_module
from custom_components.nesto.client import (
    AuthenticationFailed,
    CannotConnect,
    UnsupportedProtocol,
)
from custom_components.nesto.const import DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir

from .common import KEY, PANEL_ID, TITLE, ClientFactory, make_hello


@pytest.fixture(autouse=True)
def no_waiting():
    """Reconnect at once instead of after the backoff."""
    with patch.object(coordinator_module, "RECONNECT_DELAYS", (0,)):
        yield


def sent_types(client) -> list[str]:
    return [message["t"] for message in client.sent]


# Setting up ------------------------------------------------------------------


async def test_setup_synchronises(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    assert init_integration.state is ConfigEntryState.LOADED
    client = clients.last
    client.authenticate.assert_awaited_once_with(KEY)
    # Section 8's order: time, config, layout, states.
    assert sent_types(client) == ["time", "config", "layout", "states"]
    time, _config, layout, states = client.sent
    assert time["tz"] == hass.config.time_zone
    assert isinstance(time["utc_ms"], int)
    assert isinstance(time["utc_offset_min"], int)
    assert layout["title"] == TITLE
    assert layout["items"] == []
    assert states == {"t": "states", "full": True, "items": {}}

    coordinator = init_integration.runtime_data
    assert coordinator.data.connected
    assert not coordinator.data.ready
    client.on_message({"t": "ready"})
    assert coordinator.data.ready


async def test_setup_writes_nothing_but_the_synchronisation(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    """Never write to the panel unasked: the settings are the panel's defaults."""
    config = next(m for m in clients.last.sent if m["t"] == "config")
    # An empty config: every setting takes the firmware's default (section 12).
    assert config == {"t": "config"}
    clients.last.request.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (CannotConnect("refused"), ConfigEntryState.SETUP_RETRY),
        (AuthenticationFailed("no"), ConfigEntryState.SETUP_ERROR),
        (UnsupportedProtocol(2), ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_setup_fails(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    clients: ClientFactory,
    error: Exception,
    state: ConfigEntryState,
) -> None:
    clients.connect_error = error
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is state


async def test_a_rejected_key_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.authenticate_error = AuthenticationFailed("no")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    clients.last.close.assert_awaited()


async def test_a_forgetful_panel_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.hello = make_hello(paired=False, pairing_open=True)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    clients.last.authenticate.assert_not_awaited()


async def test_another_panel_at_the_address(
    hass: HomeAssistant, config_entry: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.hello = make_hello(panel_id="aabbccddeeff")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    clients.last.authenticate.assert_not_awaited()


async def test_an_unsupported_panel_raises_an_issue(
    hass: HomeAssistant, config_entry: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.connect_error = UnsupportedProtocol(2)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"unsupported_protocol_{config_entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_placeholders == {"name": TITLE, "version": "2"}


async def test_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    assert init_integration.state is ConfigEntryState.NOT_LOADED
    clients.last.close.assert_awaited()


# The device ------------------------------------------------------------------


async def test_device(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    registry = dr.async_get(hass)
    [device] = dr.async_entries_for_config_entry(registry, init_integration.entry_id)
    assert device.identifiers == {(DOMAIN, PANEL_ID)}
    assert device.connections == {(dr.CONNECTION_NETWORK_MAC, "18:b4:30:0a:1b:2c")}
    assert device.manufacturer == "Nesto"
    assert device.model == "Nest Learning Thermostat (2nd generation)"
    assert device.model_id == "nest-gen2"
    assert device.name == TITLE
    assert device.sw_version == "0.1.0"


# Staying connected -----------------------------------------------------------


async def test_reconnects_after_a_drop(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordinator = init_integration.runtime_data
    clients.hello = make_hello(fw="0.2.0")
    clients.connect_error = CannotConnect("refused")
    clients.drop(CannotConnect("the panel closed the connection"))
    assert not coordinator.data.connected
    await hass.async_block_till_done()
    assert "is unavailable" in caplog.text
    assert not coordinator.connected

    clients.connect_error = None
    await coordinator._reconnect_task
    assert coordinator.data.connected
    assert "is back" in caplog.text
    assert sent_types(clients.last) == ["time", "config", "layout", "states"]
    # A new layout revision, so the panel takes it as the latest (section 9).
    first_rev = clients.clients[0].sent[2]["rev"]
    assert clients.last.sent[2]["rev"] > first_rev
    # The firmware was updated meanwhile.
    [device] = dr.async_entries_for_config_entry(
        dr.async_get(hass), init_integration.entry_id
    )
    assert device.sw_version == "0.2.0"


async def test_reconnecting_finds_the_key_rejected(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    coordinator = init_integration.runtime_data
    clients.authenticate_error = AuthenticationFailed("no")
    clients.drop(CannotConnect("closed"))
    await coordinator._reconnect_task
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_reconnecting_finds_another_protocol(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    coordinator = init_integration.runtime_data
    clients.connect_error = UnsupportedProtocol(2)
    clients.drop(CannotConnect("closed"))
    await coordinator._reconnect_task
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"unsupported_protocol_{init_integration.entry_id}"
    )
    assert isinstance(coordinator.last_error, UnsupportedProtocol)


async def test_reconnect_waits_by_the_backoff(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    coordinator = init_integration.runtime_data
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == 5:
            clients.connect_error = None  # the panel is back for the fifth try

    clients.connect_error = CannotConnect("refused")
    # The module's own asyncio name, never asyncio.sleep itself, which the
    # event loop uses.
    with (
        patch.object(coordinator_module, "RECONNECT_DELAYS", (1, 2, 60)),
        patch.object(coordinator_module, "asyncio", SimpleNamespace(sleep=sleep)),
    ):
        clients.drop(CannotConnect("closed"))
        await coordinator._reconnect_task
    assert delays == [1, 2, 60, 60, 60]
    assert coordinator.data.connected


async def test_unloading_stops_reconnecting(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    coordinator = init_integration.runtime_data
    clients.connect_error = CannotConnect("refused")
    with patch.object(coordinator_module, "RECONNECT_DELAYS", (3600,)):
        clients.drop(CannotConnect("closed"))
        task = coordinator._reconnect_task
        assert await hass.config_entries.async_unload(init_integration.entry_id)
        await hass.async_block_till_done()
    assert task.cancelled() or task.done()


# Synchronising ---------------------------------------------------------------


async def test_the_time_every_hour(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    freezer: FrozenDateTimeFactory,
) -> None:
    client = clients.last
    # Jumping ahead from the real now passes earlier hours; let those go by.
    freezer.move_to("2026-11-14 10:59:59+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    client.sent.clear()

    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert sent_types(client) == ["time"]
    expected = datetime(2026, 11, 14, 11, tzinfo=UTC)
    assert client.sent[0]["utc_ms"] == int(expected.timestamp() * 1000)

    freezer.tick(timedelta(minutes=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(client.sent) == 1  # nothing on the half hour


async def test_the_hourly_time_waits_for_a_connection(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    freezer: FrozenDateTimeFactory,
) -> None:
    client = clients.last
    client.sent.clear()
    client.authenticated = False  # dropped, not yet reported
    freezer.move_to("2026-11-14 11:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert client.sent == []


async def test_the_hourly_time_survives_a_broken_send(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    freezer: FrozenDateTimeFactory,
) -> None:
    clients.last.send.side_effect = CannotConnect("broken pipe")
    freezer.move_to("2026-11-14 11:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("request_", "code"),
    [
        ({"t": "intent", "id": 7, "key": "l1", "action": "toggle"}, "unknown_key"),
        ({"t": "image_next", "id": 8}, "no_images"),
        ({"t": "frobnicate", "id": 9}, "unknown_type"),
    ],
)
async def test_the_panels_requests_are_answered(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    request_: dict,
    code: str,
) -> None:
    client = clients.last
    client.on_message(request_)
    await hass.async_block_till_done()
    client.respond.assert_awaited_once()
    assert client.respond.await_args.args[:2] == (request_["id"], code)


async def test_events_are_only_logged_for_now(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.last.on_message({"t": "sensors", "temperature_c": 21.0})
    await hass.async_block_till_done()
    clients.last.respond.assert_not_awaited()


async def test_an_answer_that_cant_be_sent(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.last.respond.side_effect = CannotConnect("closed")
    clients.last.on_message({"t": "image_next", "id": 3})
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.LOADED


# Removing --------------------------------------------------------------------


@pytest.fixture
async def loaded(
    hass: HomeAssistant, config_entry: MockConfigEntry, clients: ClientFactory
) -> MockConfigEntry:
    """Set up the integration, without unloading it afterwards: it's removed."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def test_removing_unpairs_the_panel(
    hass: HomeAssistant, loaded: MockConfigEntry, clients: ClientFactory
) -> None:
    await hass.config_entries.async_remove(loaded.entry_id)
    await hass.async_block_till_done()
    client = clients.last
    client.authenticate.assert_awaited_once_with(KEY)
    assert client.sent == [{"t": "unpair"}]
    client.close.assert_awaited()


@pytest.mark.parametrize(
    "hello",
    [make_hello(panel_id="aabbccddeeff"), make_hello(paired=False)],
)
async def test_removing_leaves_another_or_unpaired_panel_alone(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    clients: ClientFactory,
    hello,
) -> None:
    clients.hello = hello
    await hass.config_entries.async_remove(loaded.entry_id)
    await hass.async_block_till_done()
    clients.last.authenticate.assert_not_awaited()
    assert clients.last.sent == []


async def test_removing_a_panel_thats_gone(
    hass: HomeAssistant, loaded: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.connect_error = CannotConnect("no route to host")
    await hass.config_entries.async_remove(loaded.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(loaded.entry_id) is None
