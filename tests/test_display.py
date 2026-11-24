"""The display light and the settings entities (PROTOCOL.md sections 12, 13)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.nesto import coordinator as coordinator_module
from custom_components.nesto.client import CannotConnect
from custom_components.nesto.const import DOMAIN
from homeassistant.components.light import ATTR_BRIGHTNESS, DOMAIN as LIGHT_DOMAIN
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .common import ClientFactory

STORAGE_KEY = f"{DOMAIN}.{{}}"


@pytest.fixture(autouse=True)
def no_waiting():
    """Reconnect at once instead of after the backoff."""
    with patch.object(coordinator_module, "RECONNECT_DELAYS", (0,)):
        yield


def entity_id(
    hass: HomeAssistant, entry: MockConfigEntry, domain: str, key: str
) -> str:
    found = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert found is not None, key
    return found


def configs(clients: ClientFactory) -> list[dict[str, Any]]:
    return [m for m in clients.last.sent if m["t"] == "config"]


def report(clients: ClientFactory, state: str, brightness: int) -> None:
    clients.last.on_message(
        {"t": "display_state", "state": state, "brightness": brightness, "cause": "ha"}
    )


async def call(hass: HomeAssistant, domain: str, service: str, **data: Any) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)


async def saved(hass: HomeAssistant) -> None:
    """Let the store's delayed save run."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()


# The display ---------------------------------------------------------------


async def test_display_follows_the_panel(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    light = entity_id(hass, init_integration, LIGHT_DOMAIN, "display")
    # Nothing known until the panel reports, right after synchronising.
    assert hass.states.get(light).state == STATE_UNKNOWN

    report(clients, "active", 80)
    await hass.async_block_till_done()
    state = hass.states.get(light)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 204

    report(clients, "standby", 0)
    await hass.async_block_till_done()
    state = hass.states.get(light)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 0

    report(clients, "off", 0)
    await hass.async_block_till_done()
    assert hass.states.get(light).state == STATE_OFF


@pytest.mark.parametrize(
    "message",
    [
        {"t": "display_state", "state": "dim", "brightness": 10},
        {"t": "display_state", "state": "active"},
        {"t": "display_state", "state": "active", "brightness": "80"},
    ],
)
async def test_a_bad_display_state_is_ignored(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    message: dict[str, Any],
) -> None:
    report(clients, "active", 50)
    clients.last.on_message(message)
    await hass.async_block_till_done()
    assert init_integration.runtime_data.data.display.state == "active"


async def test_ready_keeps_the_display(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    report(clients, "standby", 20)
    clients.last.on_message({"t": "ready"})
    data = init_integration.runtime_data.data
    assert data.ready
    assert data.display.state == "standby"


async def test_display_unavailable_while_disconnected(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    light = entity_id(hass, init_integration, LIGHT_DOMAIN, "display")
    report(clients, "active", 80)
    clients.connect_error = CannotConnect("refused")
    clients.drop(CannotConnect("closed"))
    await hass.async_block_till_done()
    assert hass.states.get(light).state == STATE_UNAVAILABLE


async def test_turn_the_display_on_and_off(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    light = entity_id(hass, init_integration, LIGHT_DOMAIN, "display")
    await call(hass, LIGHT_DOMAIN, SERVICE_TURN_OFF, entity_id=light)
    await call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, entity_id=light)
    requests = [c.args[0] for c in clients.last.request.await_args_list]
    assert requests == [
        {"t": "display", "state": "off"},
        {"t": "display", "state": "active"},
    ]
    # Turning it on without a brightness leaves the setting alone.
    assert len(configs(clients)) == 1


async def test_a_brightness_becomes_the_setting(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    hass_storage: dict[str, Any],
) -> None:
    light = entity_id(hass, init_integration, LIGHT_DOMAIN, "display")
    number = entity_id(hass, init_integration, NUMBER_DOMAIN, "display_brightness")
    await call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, entity_id=light, brightness=128)
    assert configs(clients)[-1]["display"]["brightness"] == 50
    assert clients.last.request.await_args.args[0] == {
        "t": "display",
        "state": "active",
    }
    assert hass.states.get(number).state == "50"

    # The lowest brightness Home Assistant offers is still lit.
    await call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, entity_id=light, brightness=1)
    assert configs(clients)[-1]["display"]["brightness"] == 1


async def test_turning_the_display_fails(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    light = entity_id(hass, init_integration, LIGHT_DOMAIN, "display")
    clients.request_error = CannotConnect("no answer")
    with pytest.raises(HomeAssistantError) as caught:
        await call(hass, LIGHT_DOMAIN, SERVICE_TURN_OFF, entity_id=light)
    assert caught.value.translation_key == "cannot_send"


# Settings --------------------------------------------------------------------


async def test_settings_entities(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The unique ids are fixed: changing them orphans users' entities."""
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, init_integration.entry_id)
    config = sorted(
        e.unique_id.removeprefix(f"{init_integration.entry_id}_")
        for e in entities
        if e.entity_category is EntityCategory.CONFIG
    )
    assert config == sorted(
        [
            "identify",
            "restart",
            "firmware",
            "display_brightness",
            "standby_brightness",
            "display_timeout_s",
            "standby_mode",
            "standby_clock_style",
            "local_wake",
            "display_auto_brightness",
            "display_wake_on_motion",
            "display_wake_on_input_when_off",
            "standby_clock_seconds",
            "standby_clock_date",
            "locale_hour12",
            "sound_ring_click",
            "sound_press_click",
            "sound_notifications",
        ]
    )
    display = registry.async_get(
        entity_id(hass, init_integration, LIGHT_DOMAIN, "display")
    )
    assert display.entity_category is None


async def test_settings_show_the_defaults(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    def state(domain: str, key: str) -> str:
        return hass.states.get(entity_id(hass, init_integration, domain, key)).state

    assert state(NUMBER_DOMAIN, "display_brightness") == "80"
    assert state(NUMBER_DOMAIN, "standby_brightness") == "20"
    assert state(NUMBER_DOMAIN, "display_timeout_s") == "30"
    assert state(SELECT_DOMAIN, "standby_mode") == "clock"
    assert state(SELECT_DOMAIN, "standby_clock_style") == "analog"
    assert state(SWITCH_DOMAIN, "display_auto_brightness") == STATE_ON
    assert state(SWITCH_DOMAIN, "sound_press_click") == STATE_OFF


@pytest.mark.parametrize(
    ("domain", "key", "service", "data", "path", "value"),
    [
        (
            NUMBER_DOMAIN,
            "standby_brightness",
            SERVICE_SET_VALUE,
            {ATTR_VALUE: 35},
            ("standby", "brightness"),
            35,
        ),
        (
            NUMBER_DOMAIN,
            "display_timeout_s",
            SERVICE_SET_VALUE,
            {ATTR_VALUE: 0},
            ("display", "timeout_s"),
            0,
        ),
        (
            SELECT_DOMAIN,
            "standby_mode",
            SERVICE_SELECT_OPTION,
            {ATTR_OPTION: "off"},
            ("standby", "mode"),
            "off",
        ),
        (
            SELECT_DOMAIN,
            "standby_clock_style",
            SERVICE_SELECT_OPTION,
            {ATTR_OPTION: "digital"},
            ("standby", "clock", "style"),
            "digital",
        ),
        (
            SWITCH_DOMAIN,
            "locale_hour12",
            SERVICE_TURN_ON,
            {},
            ("locale", "hour12"),
            True,
        ),
        (
            SWITCH_DOMAIN,
            "sound_ring_click",
            SERVICE_TURN_OFF,
            {},
            ("sound", "ring_click"),
            False,
        ),
    ],
)
async def test_change_a_setting(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    hass_storage: dict[str, Any],
    domain: str,
    key: str,
    service: str,
    data: dict[str, Any],
    path: tuple[str, ...],
    value: Any,
) -> None:
    """A change is sent at once, as the whole config, and saved."""
    entity = entity_id(hass, init_integration, domain, key)
    await call(hass, domain, service, entity_id=entity, **data)

    sent = configs(clients)
    assert len(sent) == 2
    setting: Any = sent[-1]
    for name in path:
        setting = setting[name]
    assert setting == value
    # Everything else is sent too, unchanged.
    assert set(sent[-1]) == set(sent[0])

    await saved(hass)
    stored: Any = hass_storage[STORAGE_KEY.format(init_integration.entry_id)]["data"]
    for name in path:
        stored = stored[name]
    assert stored == value


async def test_the_same_value_sends_nothing(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    switch = entity_id(hass, init_integration, SWITCH_DOMAIN, "display_auto_brightness")
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=switch)
    assert len(configs(clients)) == 1


async def test_numbers_are_whole(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    number = entity_id(hass, init_integration, NUMBER_DOMAIN, "display_brightness")
    await call(hass, NUMBER_DOMAIN, SERVICE_SET_VALUE, entity_id=number, value=42.6)
    assert configs(clients)[-1]["display"]["brightness"] == 43


async def test_settings_while_the_panel_is_away(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    """A change is saved while the panel is away, and goes when it's back."""
    coordinator = init_integration.runtime_data
    switch = entity_id(hass, init_integration, SWITCH_DOMAIN, "standby_clock_seconds")
    clients.connect_error = CannotConnect("refused")
    clients.drop(CannotConnect("closed"))
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == STATE_OFF  # still available

    def sent_anywhere() -> int:
        return sum(m["t"] == "config" for c in clients.clients for m in c.sent)

    before = sent_anywhere()
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=switch)
    assert hass.states.get(switch).state == STATE_ON
    assert sent_anywhere() == before

    clients.connect_error = None
    await coordinator._reconnect_task
    assert configs(clients)[0]["standby"]["clock"]["seconds"] is True


async def test_a_broken_send_keeps_the_setting(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    switch = entity_id(hass, init_integration, SWITCH_DOMAIN, "standby_clock_date")
    clients.last.send.side_effect = CannotConnect("broken pipe")
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=switch)
    assert hass.states.get(switch).state == STATE_OFF


async def test_stored_settings_are_sent(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    clients: ClientFactory,
    hass_storage: dict[str, Any],
) -> None:
    """Stored settings are sent; anything unknown or of the wrong type isn't."""
    hass_storage[STORAGE_KEY.format(config_entry.entry_id)] = {
        "version": 1,
        "key": STORAGE_KEY.format(config_entry.entry_id),
        "data": {
            "display": {"brightness": 55, "timeout_s": "forever", "glow": True},
            "standby": {"mode": "off", "clock": "digital"},
            "sound": {"press_click": True},
            "carousel": {"interval_s": 5},
        },
    }
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    [config] = configs(clients)
    assert config["display"]["brightness"] == 55
    assert config["display"]["timeout_s"] == 30
    assert "glow" not in config["display"]
    assert config["standby"]["mode"] == "off"
    assert config["standby"]["clock"]["style"] == "analog"
    assert config["sound"]["press_click"] is True
    assert "carousel" not in config
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_removing_the_panel_deletes_its_settings(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    clients: ClientFactory,
    hass_storage: dict[str, Any],
) -> None:
    # Set up by hand: the entry is removed, so there's nothing to unload after.
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    key = STORAGE_KEY.format(config_entry.entry_id)
    switch = entity_id(hass, config_entry, SWITCH_DOMAIN, "locale_hour12")
    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=switch)
    await saved(hass)
    assert key in hass_storage

    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
