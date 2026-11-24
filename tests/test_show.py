"""The item the panel wakes on, and the show action (PROTOCOL.md sections 9, 13)."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.nesto.client import PanelError
from custom_components.nesto.const import (
    CONF_ALL_LIGHTS,
    CONF_MODE,
    DOMAIN,
    MODE_MANUAL,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    ATTR_OPTIONS,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .common import ClientFactory, make_hello

STATES = {
    "light.lamp": ("on", {"friendly_name": "Lamp"}),
    "light.ceiling": ("off", {"friendly_name": "Ceiling"}),
    "switch.plug_a": ("off", {"friendly_name": "Plug"}),
    "switch.plug_b": ("on", {"friendly_name": "Plug"}),
    "switch.other": ("on", {"friendly_name": "Elsewhere"}),
}
OPTIONS = {
    CONF_MODE: MODE_MANUAL,
    CONF_ALL_LIGHTS: True,
    "lights": ["light.lamp", "light.ceiling"],
    "switches": ["switch.plug_a", "switch.plug_b"],
}
CAPS = {"beeper": True, "input": ["ring", "press"], "show": True}


async def set_up(
    hass: HomeAssistant, entry: MockConfigEntry, clients: ClientFactory, caps: dict
) -> None:
    for entity_id, (state, attributes) in STATES.items():
        hass.states.async_set(entity_id, state, attributes)
    clients.hello = make_hello(fw="0.3.0", caps=caps)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options=OPTIONS)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
async def panel(
    hass: HomeAssistant, config_entry: MockConfigEntry, clients: ClientFactory
) -> MockConfigEntry:
    """A panel that can show items, with lights, All lights and two plugs."""
    await set_up(hass, config_entry, clients, CAPS)
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


def wake_entity(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    found = er.async_get(hass).async_get_entity_id(
        SELECT_DOMAIN, DOMAIN, f"{entry.entry_id}_local_wake"
    )
    assert found is not None
    return found


def layout(clients: ClientFactory) -> dict[str, Any]:
    return next(m for m in reversed(clients.last.sent) if m["t"] == "layout")


def key_of(clients: ClientFactory, name: str) -> str:
    return next(i["key"] for i in layout(clients)["items"] if i["name"] == name)


async def choose(hass: HomeAssistant, entity_id: str, option: str) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {"entity_id": entity_id, ATTR_OPTION: option},
        blocking=True,
    )
    await hass.async_block_till_done()


async def show(hass: HomeAssistant, entry: MockConfigEntry, **fields: Any) -> None:
    await hass.services.async_call(
        DOMAIN, "show", {"config_entry_id": entry.entry_id, **fields}, blocking=True
    )


# Wake on --------------------------------------------------------------------------


async def test_wake_on_offers_the_panels_items(
    hass: HomeAssistant, panel: MockConfigEntry, clients: ClientFactory
) -> None:
    state = hass.states.get(wake_entity(hass, panel))
    assert state.state == "first"
    # Two items with one name are told apart by their entities.
    assert state.attributes[ATTR_OPTIONS] == [
        "first",
        "All lights",
        "Lamp",
        "Ceiling",
        "Plug (switch.plug_a)",
        "Plug (switch.plug_b)",
    ]
    assert "wake" not in layout(clients)


async def test_choosing_an_item_sends_the_room_with_it(
    hass: HomeAssistant, panel: MockConfigEntry, clients: ClientFactory
) -> None:
    entity_id = wake_entity(hass, panel)
    await choose(hass, entity_id, "Ceiling")
    assert hass.states.get(entity_id).state == "Ceiling"
    assert layout(clients)["wake"] == key_of(clients, "Ceiling")
    assert panel.runtime_data.settings.get(("local", "wake")) == "light.ceiling"

    # The second plug: the last item.
    await choose(hass, entity_id, "Plug (switch.plug_b)")
    assert layout(clients)["wake"] == layout(clients)["items"][-1]["key"]

    await choose(hass, entity_id, "All lights")
    assert layout(clients)["wake"] == "all"

    await choose(hass, entity_id, "first")
    assert "wake" not in layout(clients)


async def test_wake_on_falls_back_while_its_item_is_away(
    hass: HomeAssistant, panel: MockConfigEntry, clients: ClientFactory
) -> None:
    entity_id = wake_entity(hass, panel)
    await choose(hass, entity_id, "Ceiling")
    # Gone from Home Assistant, so from the panel.
    hass.states.async_remove("light.ceiling")
    await hass.async_block_till_done()
    assert "Ceiling" not in [i["name"] for i in layout(clients)["items"]]
    assert "wake" not in layout(clients)
    assert hass.states.get(entity_id).state == "first"
    # Kept, for when it's back.
    assert panel.runtime_data.settings.get(("local", "wake")) == "light.ceiling"
    hass.states.async_set("light.ceiling", *STATES["light.ceiling"])
    await hass.async_block_till_done()
    assert layout(clients)["wake"] == key_of(clients, "Ceiling")
    assert hass.states.get(entity_id).state == "Ceiling"


async def test_wake_on_needs_a_panel_that_shows(
    hass: HomeAssistant, config_entry: MockConfigEntry, clients: ClientFactory
) -> None:
    await set_up(hass, config_entry, clients, {"beeper": True, "input": ["ring"]})
    assert hass.states.get(wake_entity(hass, config_entry)).state == STATE_UNAVAILABLE
    with pytest.raises(ServiceValidationError) as caught:
        await show(hass, config_entry, entity_id="light.lamp")
    assert caught.value.translation_key == "show_unsupported"
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


# The show action ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "name", "opened"),
    [
        ({"entity_id": "light.ceiling"}, "Ceiling", False),
        ({"entity_id": "light.lamp", "open": True}, "Lamp", True),
    ],
)
async def test_show(
    hass: HomeAssistant,
    panel: MockConfigEntry,
    clients: ClientFactory,
    fields: dict[str, Any],
    name: str,
    opened: bool,
) -> None:
    await show(hass, panel, **fields)
    assert clients.last.request.await_args.args[0] == {
        "t": "show",
        "key": key_of(clients, name),
        "open": opened,
    }


async def test_show_an_entity_not_on_the_panel(
    hass: HomeAssistant, panel: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError) as caught:
        await show(hass, panel, entity_id="switch.other")
    assert caught.value.translation_key == "not_on_panel"
    assert caught.value.translation_placeholders["entity_id"] == "switch.other"


async def test_show_refused_by_the_panel(
    hass: HomeAssistant, panel: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.request_error = PanelError("unknown_key", "no item e9")
    with pytest.raises(HomeAssistantError) as caught:
        await show(hass, panel, entity_id="light.lamp")
    assert caught.value.translation_key == "cannot_send"


async def test_show_on_a_panel_not_set_up(
    hass: HomeAssistant, panel: MockConfigEntry
) -> None:
    await hass.config_entries.async_unload(panel.entry_id)
    with pytest.raises(ServiceValidationError) as caught:
        await show(hass, panel, entity_id="light.lamp")
    assert caught.value.translation_key == "panel_not_loaded"
