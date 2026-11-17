"""The options flow: what the panel shows, section by section, in order."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.nesto.const import (
    CONF_ALL_LIGHTS,
    CONF_AREA,
    CONF_ENTITIES,
    CONF_MODE,
    CONF_REMOVED,
    DOMAIN,
    MODE_MANUAL,
    MODE_ROOM,
)
from custom_components.nesto.room import SECTIONS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, device_registry as dr

from .common import PANEL_ID, ClientFactory
from .test_room import register


async def move_panel(
    hass: HomeAssistant, entry: MockConfigEntry, area_id: str | None
) -> None:
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, PANEL_ID), entry.entry_id)
    assert device is not None
    devices.async_update_device(device.id, area_id=area_id)
    await hass.async_block_till_done()


@pytest.fixture
def living(hass: HomeAssistant) -> ar.AreaEntry:
    """A living room with two lights, a switch and a sensor."""
    area = ar.async_get(hass).async_create("Living room")
    for entity_id, name in (
        ("light.lamp", "Lamp"),
        ("light.ceiling", "Ceiling"),
        ("switch.plug", "Plug"),
        ("sensor.co2", "CO2"),
    ):
        register(hass, entity_id, area.id)
        hass.states.async_set(entity_id, "on", {"friendly_name": name})
    return area


async def choose(hass: HomeAssistant, entry: MockConfigEntry, mode: str) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MODE: mode}
    )


def shown(result: dict) -> dict[str, dict[str, Any]]:
    """Return each section's suggested values, and whether it starts folded."""
    sections: dict[str, dict[str, Any]] = {}
    for key, value in result["data_schema"].schema.items():
        fields = {
            str(field): field.description["suggested_value"]
            for field in value.schema.schema
        }
        sections[str(key)] = {**fields, "collapsed": value.options["collapsed"]}
    return sections


def answer(all_lights: bool = False, **lists: list[str]) -> dict[str, Any]:
    """Return the form's input: every section, with these entities."""
    data: dict[str, Any] = {
        name: {CONF_ENTITIES: lists.get(name, [])} for name in SECTIONS
    }
    data["lights"][CONF_ALL_LIGHTS] = all_lights
    return data


def empty_lists() -> dict[str, list[str]]:
    return {name: [] for name in SECTIONS}


async def test_room_starts_with_the_area(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    living: ar.AreaEntry,
) -> None:
    await move_panel(hass, init_integration, living.id)
    result = await choose(hass, init_integration, MODE_ROOM)
    assert result["step_id"] == "room"
    assert result["description_placeholders"] == {"area": "Living room"}
    sections = shown(result)
    assert list(sections) == list(SECTIONS)
    # By name; sensors aren't a room's unless picked; empty sections folded.
    assert sections["lights"] == {
        CONF_ENTITIES: ["light.ceiling", "light.lamp"],
        CONF_ALL_LIGHTS: False,
        "collapsed": False,
    }
    assert sections["switches"] == {
        CONF_ENTITIES: ["switch.plug"],
        "collapsed": False,
    }
    assert sections["climate"] == {CONF_ENTITIES: [], "collapsed": True}
    assert sections["sensors"] == {CONF_ENTITIES: [], "collapsed": True}

    connections = len(clients.clients)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        answer(True, lights=["light.lamp"], sensors=["sensor.co2"]),
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {
        CONF_MODE: MODE_ROOM,
        **empty_lists(),
        "lights": ["light.lamp"],
        "sensors": ["sensor.co2"],
        CONF_ALL_LIGHTS: True,
        CONF_AREA: living.id,
        CONF_REMOVED: ["light.ceiling", "switch.plug"],
    }
    # Saving reloads the entry, which connects and sends the new layout.
    assert init_integration.state is ConfigEntryState.LOADED
    assert len(clients.clients) == connections + 1


async def test_room_shows_what_was_saved_and_what_is_new(
    hass: HomeAssistant, init_integration: MockConfigEntry, living: ar.AreaEntry
) -> None:
    await move_panel(hass, init_integration, living.id)
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_MODE: MODE_ROOM,
            **empty_lists(),
            "lights": ["light.lamp"],
            CONF_ALL_LIGHTS: True,
            CONF_AREA: living.id,
            CONF_REMOVED: ["light.ceiling"],
        },
    )
    register(hass, "light.desk", living.id)
    hass.states.async_set("light.desk", "on", {"friendly_name": "Desk"})
    sections = shown(await choose(hass, init_integration, MODE_ROOM))
    # The saved order, then the new light; the one taken off stays off.
    assert sections["lights"] == {
        CONF_ENTITIES: ["light.lamp", "light.desk"],
        CONF_ALL_LIGHTS: True,
        "collapsed": False,
    }
    assert sections["switches"][CONF_ENTITIES] == ["switch.plug"]


async def test_room_without_an_area(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    hass.states.async_set("switch.kitchen", "off")
    result = await choose(hass, init_integration, MODE_ROOM)
    assert result["step_id"] == "room_no_area"
    assert all(not s[CONF_ENTITIES] for s in shown(result).values())
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], answer(switches=["switch.kitchen"])
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {
        CONF_MODE: MODE_ROOM,
        **empty_lists(),
        "switches": ["switch.kitchen"],
        CONF_ALL_LIGHTS: False,
        CONF_AREA: None,
        CONF_REMOVED: [],
    }


async def test_manual_starts_empty(
    hass: HomeAssistant, init_integration: MockConfigEntry, living: ar.AreaEntry
) -> None:
    await move_panel(hass, init_integration, living.id)
    result = await choose(hass, init_integration, MODE_MANUAL)
    assert result["step_id"] == "manual"
    assert all(not s[CONF_ENTITIES] for s in shown(result).values())
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        answer(lights=["light.lamp", "light.ceiling", "light.lamp"]),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {
        CONF_MODE: MODE_MANUAL,
        **empty_lists(),
        "lights": ["light.lamp", "light.ceiling"],  # each once
        CONF_ALL_LIGHTS: False,
    }
    await hass.async_block_till_done()

    # Opened again, it shows what was saved.
    sections = shown(await choose(hass, init_integration, MODE_MANUAL))
    assert sections["lights"][CONF_ENTITIES] == ["light.lamp", "light.ceiling"]
    # Room mode from there starts with the area, not the manual lists.
    sections = shown(await choose(hass, init_integration, MODE_ROOM))
    assert sections["lights"][CONF_ENTITIES] == ["light.ceiling", "light.lamp"]
    assert sections["switches"][CONF_ENTITIES] == ["switch.plug"]


@pytest.mark.parametrize("mode", [MODE_ROOM, MODE_MANUAL])
async def test_the_mode_starts_from_the_current_options(
    hass: HomeAssistant, init_integration: MockConfigEntry, mode: str
) -> None:
    hass.config_entries.async_update_entry(init_integration, options={CONF_MODE: mode})
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    suggested = {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
    }
    assert suggested == {CONF_MODE: mode}
    await hass.async_block_till_done()
