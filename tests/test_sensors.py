"""The sensors, the panel's status and its press gestures (PROTOCOL.md 17, 18)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.nesto import coordinator as coordinator_module
from custom_components.nesto.client import CannotConnect
from custom_components.nesto.const import DOMAIN
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.event import ATTR_EVENT_TYPE, DOMAIN as EVENT_DOMAIN
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .common import ClientFactory, make_hello

BASE_SENSORS = [
    "temperature",
    "humidity",
    "illuminance",
    "motion_near",
    "motion_far",
    "supply_voltage",
    "usb_voltage",
    "battery_voltage",
]
READINGS = {
    "t": "sensors",
    "temperature_c": 21.4,
    "humidity_pct": 46.0,
    "illuminance_raw": 312,
    "motion_near": False,
    "motion_far": True,
    "supply_v": 0.0,
    "usb_v": 4.94,
    "battery_v": 3.91,
}
INFO = {
    "t": "result",
    "ok": True,
    "uptime_s": 3600,
    "free_ram_kb": 61234,
    "wifi_rssi": -51,
    "ip": "192.168.1.60",
    "fw": "0.1.0",
    "base_connected": True,
    "base_fw": "1.2.3",
}


def with_base() -> Any:
    return make_hello(
        caps={"beeper": True, "input": ["ring", "press"], "sensors": BASE_SENSORS}
    )


@pytest.fixture(autouse=True)
def no_waiting():
    """Reconnect at once instead of after the backoff."""
    with patch.object(coordinator_module, "RECONNECT_DELAYS", (0,)):
        yield


@pytest.fixture
async def on_base(
    hass: HomeAssistant, config_entry: MockConfigEntry, clients: ClientFactory
) -> AsyncGenerator[MockConfigEntry]:
    """Set up a panel on its base, with every sensor."""
    clients.hello = with_base()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


def entity_id(
    hass: HomeAssistant, entry: MockConfigEntry, domain: str, key: str
) -> str:
    found = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert found is not None, key
    return found


def keys(hass: HomeAssistant, entry: MockConfigEntry, domain: str) -> set[str]:
    return {
        e.unique_id.removeprefix(f"{entry.entry_id}_")
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.domain == domain
    }


async def send(hass: HomeAssistant, clients: ClientFactory, message: dict) -> None:
    clients.last.on_message(message)
    await hass.async_block_till_done(wait_background_tasks=True)


def answer_info(clients: ClientFactory, info: dict[str, Any] = INFO) -> None:
    """Make the panel answer `info` requests with `info`."""

    async def request(message: dict[str, Any], _seconds: float | None = None):
        clients.last.sent.append(message)
        if isinstance(info, Exception):
            raise info
        return info

    clients.last.request.side_effect = request


# Which entities ----------------------------------------------------------------


async def test_a_panel_off_its_base_has_no_sensors(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Only the status, which every panel reports."""
    assert keys(hass, init_integration, SENSOR_DOMAIN) == {
        "last_restart",
        "free_memory",
        "wifi_signal",
        "ip_address",
        "base_firmware",
    }
    assert keys(hass, init_integration, BINARY_SENSOR_DOMAIN) == {"base_connected"}
    assert "local_temperature_offset" not in keys(hass, init_integration, NUMBER_DOMAIN)


async def test_a_panel_on_its_base(
    hass: HomeAssistant, on_base: MockConfigEntry
) -> None:
    """The unique ids are fixed: changing them orphans users' entities."""
    assert keys(hass, on_base, SENSOR_DOMAIN) == {
        "temperature",
        "humidity",
        "illuminance",
        "battery_voltage",
        "supply_voltage",
        "usb_voltage",
        "last_restart",
        "free_memory",
        "wifi_signal",
        "ip_address",
        "base_firmware",
    }
    assert keys(hass, on_base, BINARY_SENSOR_DOMAIN) == {
        "motion",
        "proximity",
        "base_connected",
    }
    registry = er.async_get(hass)
    offset = registry.async_get(
        entity_id(hass, on_base, NUMBER_DOMAIN, "local_temperature_offset")
    )
    assert offset.entity_category is EntityCategory.CONFIG
    for key, disabled in (
        ("temperature", False),
        ("battery_voltage", False),
        ("supply_voltage", True),
        ("usb_voltage", True),
        ("free_memory", True),
        ("wifi_signal", True),
        ("ip_address", True),
        ("base_firmware", True),
        ("last_restart", False),
    ):
        entry = registry.async_get(entity_id(hass, on_base, SENSOR_DOMAIN, key))
        assert (entry.disabled_by is not None) is disabled, key
    battery = registry.async_get(
        entity_id(hass, on_base, SENSOR_DOMAIN, "battery_voltage")
    )
    assert battery.entity_category is EntityCategory.DIAGNOSTIC


async def test_other_sensors_reload_the_entry(
    hass: HomeAssistant, on_base: MockConfigEntry, clients: ClientFactory
) -> None:
    """A panel taken off its base reconnects with no sensors: entities go."""
    coordinator = on_base.runtime_data
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        clients.hello = make_hello()
        clients.drop(CannotConnect("closed"))
        await coordinator._reconnect_task
    reload.assert_called_once_with(on_base.entry_id)


async def test_the_same_sensors_reload_nothing(
    hass: HomeAssistant, on_base: MockConfigEntry, clients: ClientFactory
) -> None:
    coordinator = on_base.runtime_data
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        clients.drop(CannotConnect("closed"))
        await coordinator._reconnect_task
    reload.assert_not_called()


# Readings ----------------------------------------------------------------------


async def test_readings(
    hass: HomeAssistant, on_base: MockConfigEntry, clients: ClientFactory
) -> None:
    def state(domain: str, key: str) -> str:
        return hass.states.get(entity_id(hass, on_base, domain, key)).state

    assert state(SENSOR_DOMAIN, "temperature") == STATE_UNKNOWN
    assert state(BINARY_SENSOR_DOMAIN, "motion") == STATE_UNKNOWN

    await send(hass, clients, READINGS)
    assert state(SENSOR_DOMAIN, "temperature") == "21.4"
    assert state(SENSOR_DOMAIN, "humidity") == "46.0"
    assert state(SENSOR_DOMAIN, "illuminance") == "312"
    assert state(SENSOR_DOMAIN, "battery_voltage") == "3.91"
    assert state(BINARY_SENSOR_DOMAIN, "motion") == STATE_ON
    assert state(BINARY_SENSOR_DOMAIN, "proximity") == STATE_OFF

    # A sensor left out, or sent as something it isn't, isn't known.
    await send(
        hass,
        clients,
        {"t": "sensors", "temperature_c": "warm", "humidity_pct": 50, "motion_far": 1},
    )
    assert state(SENSOR_DOMAIN, "temperature") == STATE_UNKNOWN
    assert state(SENSOR_DOMAIN, "humidity") == "50.0"
    assert state(BINARY_SENSOR_DOMAIN, "motion") == STATE_UNKNOWN
    assert state(SENSOR_DOMAIN, "illuminance") == STATE_UNKNOWN


async def test_readings_unavailable_while_disconnected(
    hass: HomeAssistant, on_base: MockConfigEntry, clients: ClientFactory
) -> None:
    await send(hass, clients, READINGS)
    clients.connect_error = CannotConnect("refused")
    clients.drop(CannotConnect("closed"))
    await hass.async_block_till_done()
    temperature = entity_id(hass, on_base, SENSOR_DOMAIN, "temperature")
    assert hass.states.get(temperature).state == STATE_UNAVAILABLE


async def test_temperature_offset(
    hass: HomeAssistant,
    on_base: MockConfigEntry,
    clients: ClientFactory,
    hass_storage: dict[str, Any],
) -> None:
    """The offset changes the reading at once, and is never sent to the panel."""
    temperature = entity_id(hass, on_base, SENSOR_DOMAIN, "temperature")
    offset = entity_id(hass, on_base, NUMBER_DOMAIN, "local_temperature_offset")
    await send(hass, clients, READINGS)
    assert hass.states.get(offset).state == "0.0"

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {"entity_id": offset, ATTR_VALUE: -2.34},
        blocking=True,
    )
    assert hass.states.get(offset).state == "-2.3"
    assert hass.states.get(temperature).state == "19.1"
    configs = [m for m in clients.last.sent if m["t"] == "config"]
    assert len(configs) == 1
    assert "local" not in configs[0]

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()
    stored = hass_storage[f"{DOMAIN}.{on_base.entry_id}"]["data"]
    assert stored["local"] == {"temperature_offset": -2.3, "wake": ""}


async def test_a_stored_whole_offset(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    clients: ClientFactory,
    hass_storage: dict[str, Any],
) -> None:
    """An offset stored as a whole number is taken as one."""
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}",
        "data": {"local": {"temperature_offset": -2}},
    }
    clients.hello = with_base()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await send(hass, clients, READINGS)
    temperature = entity_id(hass, config_entry, SENSOR_DOMAIN, "temperature")
    assert hass.states.get(temperature).state == "19.4"
    await hass.config_entries.async_unload(config_entry.entry_id)


# Press gestures ----------------------------------------------------------------


async def test_presses(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    press = entity_id(hass, init_integration, EVENT_DOMAIN, "press")
    gesture = {"t": "input", "gesture": "double", "context": "standby"}
    # Not before the panel is ready (section 8).
    await send(hass, clients, gesture)
    assert hass.states.get(press).state == STATE_UNKNOWN

    await send(hass, clients, {"t": "ready"})
    await send(hass, clients, gesture)
    state = hass.states.get(press)
    assert state.attributes[ATTR_EVENT_TYPE] == "double_press"
    assert state.attributes["context"] == "standby"

    await send(hass, clients, {"t": "input", "gesture": "long", "context": "ui"})
    state = hass.states.get(press)
    assert state.attributes[ATTR_EVENT_TYPE] == "long_press"
    assert state.attributes["context"] == "ui"


@pytest.mark.parametrize(
    "message",
    [
        {"t": "input", "gesture": "triple", "context": "ui"},
        {"t": "input", "gesture": "single", "context": "elsewhere"},
        {"t": "input"},
    ],
)
async def test_a_bad_press_is_ignored(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    message: dict[str, Any],
) -> None:
    press = entity_id(hass, init_integration, EVENT_DOMAIN, "press")
    await send(hass, clients, {"t": "ready"})
    await send(hass, clients, message)
    assert hass.states.get(press).state == STATE_UNKNOWN


# The panel's status --------------------------------------------------------------


async def test_status_when_ready(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    freezer: FrozenDateTimeFactory,
) -> None:
    def state(domain: str, key: str) -> str:
        return hass.states.get(entity_id(hass, init_integration, domain, key)).state

    freezer.move_to("2026-11-16 10:00:00+00:00")
    answer_info(clients)
    assert state(SENSOR_DOMAIN, "last_restart") == STATE_UNKNOWN
    await send(hass, clients, {"t": "ready"})
    assert clients.last.sent[-1] == {"t": "info"}
    assert state(SENSOR_DOMAIN, "last_restart") == "2026-11-16T09:00:00+00:00"
    assert state(BINARY_SENSOR_DOMAIN, "base_connected") == STATE_ON
    status = init_integration.runtime_data.data.status
    assert status.free_ram_kb == 61234
    assert status.wifi_rssi == -51
    assert status.ip == "192.168.1.60"
    assert status.base_fw == "1.2.3"


async def test_status_every_five_minutes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A restart time that moves by seconds is the same restart."""
    coordinator = init_integration.runtime_data
    freezer.move_to("2026-11-16 10:00:00+00:00")
    answer_info(clients)
    await send(hass, clients, {"t": "ready"})
    first = coordinator.data.status.last_restart

    # Five minutes on, the panel counts 299 s more: two seconds' drift.
    answer_info(clients, {**INFO, "uptime_s": 3600 + 298, "free_ram_kb": 60000})
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.data.status.last_restart == first
    assert coordinator.data.status.free_ram_kb == 60000

    # The panel restarted: its uptime is short.
    answer_info(clients, {**INFO, "uptime_s": 10})
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.data.status.last_restart == dt_util.utcnow() - timedelta(
        seconds=10
    )


async def test_status_not_asked_before_ready(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    clients.last.request.assert_not_awaited()


async def test_status_with_odd_fields(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    answer_info(
        clients,
        {
            "t": "result",
            "ok": True,
            "uptime_s": True,
            "free_ram_kb": "lots",
            "wifi_rssi": -51.5,
            "ip": 7,
        },
    )
    await send(hass, clients, {"t": "ready"})
    status = init_integration.runtime_data.data.status
    assert status.last_restart is None
    assert status.free_ram_kb is None
    assert status.wifi_rssi is None
    assert status.ip is None
    assert status.base_connected is None


async def test_status_that_fails(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    answer_info(clients, CannotConnect("no answer"))
    await send(hass, clients, {"t": "ready"})
    assert init_integration.runtime_data.data.status is None


async def test_status_after_the_connection_dropped(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    """An answer arriving after the drop isn't kept."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(replace(coordinator.data, ready=True))

    async def request(message: dict[str, Any], _seconds: float | None = None):
        clients.drop(CannotConnect("closed"))
        return INFO

    clients.last.request.side_effect = request
    # The reconnection waits, so the panel stays away.
    with patch.object(coordinator_module, "RECONNECT_DELAYS", (3600,)):
        await coordinator._async_refresh_status()
    assert coordinator.data.status is None
