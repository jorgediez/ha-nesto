"""The options flow: what the panel shows."""

from __future__ import annotations

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.nesto.const import (
    CONF_ENTITIES,
    CONF_EXCLUDE,
    CONF_MODE,
    CONF_SENSORS,
    DOMAIN,
    MODE_MANUAL,
    MODE_ROOM,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, device_registry as dr

from .common import PANEL_ID, ClientFactory


async def move_panel(hass: HomeAssistant, entry: MockConfigEntry, area_id: str) -> None:
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, PANEL_ID), entry.entry_id)
    assert device is not None
    devices.async_update_device(device.id, area_id=area_id)
    await hass.async_block_till_done()


async def choose(hass: HomeAssistant, entry: MockConfigEntry, mode: str) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MODE: mode}
    )


async def test_room_in_an_area(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    living = ar.async_get(hass).async_create("Living room")
    await move_panel(hass, init_integration, living.id)
    result = await choose(hass, init_integration, MODE_ROOM)
    assert result["step_id"] == "room"
    assert result["description_placeholders"] == {"area": "Living room"}
    assert CONF_EXCLUDE in result["data_schema"].schema

    connections = len(clients.clients)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_EXCLUDE: ["light.lamp"], CONF_SENSORS: ["sensor.co2"]},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {
        CONF_MODE: MODE_ROOM,
        CONF_EXCLUDE: ["light.lamp"],
        CONF_SENSORS: ["sensor.co2"],
    }
    # Saving reloads the entry, which connects and sends the new layout.
    assert init_integration.state is ConfigEntryState.LOADED
    assert len(clients.clients) == connections + 1


async def test_room_without_an_area(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    result = await choose(hass, init_integration, MODE_ROOM)
    assert result["step_id"] == "room_no_area"
    assert CONF_EXCLUDE not in result["data_schema"].schema
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {
        CONF_MODE: MODE_ROOM,
        CONF_EXCLUDE: [],
        CONF_SENSORS: [],
    }


async def test_manual(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    result = await choose(hass, init_integration, MODE_MANUAL)
    assert result["step_id"] == "manual"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ENTITIES: ["light.lamp", "sensor.co2"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {
        CONF_MODE: MODE_MANUAL,
        CONF_ENTITIES: ["light.lamp", "sensor.co2"],
    }


@pytest.mark.parametrize("mode", [MODE_ROOM, MODE_MANUAL])
async def test_the_form_starts_from_the_current_options(
    hass: HomeAssistant, init_integration: MockConfigEntry, mode: str
) -> None:
    hass.config_entries.async_update_entry(
        init_integration, options={CONF_MODE: mode, CONF_ENTITIES: ["light.lamp"]}
    )
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    suggested = {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
    }
    assert suggested == {CONF_MODE: mode}
    await hass.async_block_till_done()
