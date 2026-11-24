"""The room: which entities the panel shows, their states, and its intents."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.nesto import room
from custom_components.nesto.client import CannotConnect
from custom_components.nesto.const import (
    CONF_ALL_LIGHTS,
    CONF_AREA,
    CONF_MODE,
    CONF_REMOVED,
    DOMAIN,
    MODE_MANUAL,
    MODE_ROOM,
)
from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.entity import EntityCategory

from .common import PANEL_ID, TITLE, ClientFactory, make_hello

LIGHT_ATTRS = {
    "friendly_name": "Living room Ceiling",
    "supported_color_modes": ["color_temp", "hs"],
    "color_mode": "color_temp",
    "brightness": 128,
    "color_temp_kelvin": 2700,
    "min_color_temp_kelvin": 2200,
    "max_color_temp_kelvin": 6500,
    "hs_color": [30.4, 50.6],
    "effect_list": ["None", "Candle", "Fireplace"],
    "effect": "None",
}
# entity id: (state, attributes)
ROOM: dict[str, tuple[str, dict[str, Any]]] = {
    "light.ceiling": ("on", LIGHT_ATTRS),
    "light.lamp": (
        "off",
        {"friendly_name": "Reading lamp", "supported_color_modes": ["onoff"]},
    ),
    "climate.radiator": (
        "heat",
        {
            "friendly_name": "Radiator",
            "supported_features": 1 | 16,
            "hvac_modes": ["off", "heat", "auto"],
            "preset_modes": ["eco", "comfort"],
            "min_temp": 5,
            "max_temp": 30,
            "current_temperature": 20.5,
            "temperature": 21,
            "hvac_action": "heating",
            "preset_mode": "eco",
        },
    ),
    "cover.blind": (
        "open",
        {
            "friendly_name": "Window blind",
            "supported_features": 1 | 2 | 4 | 8,
            "device_class": "blind",
            "current_position": 70,
        },
    ),
    "switch.fan_plug": (
        "off",
        {"friendly_name": "Fan plug", "device_class": "outlet"},
    ),
    "fan.simple": ("on", {"friendly_name": "Ceiling fan", "supported_features": 0}),
    "fan.speeds": ("on", {"friendly_name": "Speed fan", "supported_features": 1}),
    "scene.film": ("2026-11-15T10:00:00", {"friendly_name": "Film night"}),
    "script.bedtime": ("off", {"friendly_name": "Bedtime"}),
    "sensor.co2": (
        "612.345",
        {"friendly_name": "CO2", "unit_of_measurement": "ppm"},
    ),
}


def register(
    hass: HomeAssistant, entity_id: str, area_id: str | None, **kwargs: Any
) -> er.RegistryEntry:
    domain, object_id = entity_id.split(".")
    entities = er.async_get(hass)
    entry = entities.async_get_or_create(
        domain, "demo", object_id, suggested_object_id=object_id, **kwargs
    )
    assert entry.entity_id == entity_id
    return entities.async_update_entity(entity_id, area_id=area_id)


async def put_panel_in(hass: HomeAssistant, area_id: str | None) -> None:
    devices = dr.async_get(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    device = devices.async_get_device_by_identifier((DOMAIN, PANEL_ID), entry.entry_id)
    assert device is not None
    devices.async_update_device(device.id, area_id=area_id)
    await hass.async_block_till_done()


@pytest.fixture
def area(hass: HomeAssistant) -> ar.AreaEntry:
    """A living room with something of every kind in it."""
    living = ar.async_get(hass).async_create("Living room")
    for entity_id, (state, attributes) in ROOM.items():
        register(hass, entity_id, living.id)
        hass.states.async_set(entity_id, state, attributes)
    # Not controls for a room.
    register(
        hass, "switch.child_lock", living.id, entity_category=EntityCategory.CONFIG
    )
    hass.states.async_set("switch.child_lock", "off")
    register(hass, "switch.hidden", living.id, hidden_by=er.RegistryEntryHider.USER)
    hass.states.async_set("switch.hidden", "off")
    # Elsewhere.
    register(hass, "switch.kitchen", None)
    hass.states.async_set("switch.kitchen", "off")
    return living


@pytest.fixture
async def in_room(
    hass: HomeAssistant,
    area: ar.AreaEntry,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
) -> MockConfigEntry:
    """The panel, set up with an "All lights" item, then moved into the room."""
    hass.config_entries.async_update_entry(
        init_integration, options={CONF_ALL_LIGHTS: True}
    )
    clients.last.sent.clear()
    await put_panel_in(hass, area.id)
    return init_integration


def last(client, kind: str) -> dict[str, Any]:
    return next(m for m in reversed(client.sent) if m["t"] == kind)


def items_by_name(layout: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in layout["items"]}


# Which entities ----------------------------------------------------------------


async def test_moving_the_panel_shows_its_room(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    client = clients.last
    assert [m["t"] for m in client.sent] == ["layout", "states"]
    layout = last(client, "layout")
    assert layout["title"] == "Living room"
    # Appendix A's order; the area's name left off the front of names.
    assert [(i["name"], i["kind"]) for i in layout["items"]] == [
        ("All lights", "light"),
        ("Ceiling", "light"),
        ("Reading lamp", "light"),
        ("Radiator", "climate"),
        ("Window blind", "cover"),
        # A panel that doesn't list `fan` in caps.kinds gets switches.
        ("Ceiling fan", "switch"),
        ("Fan plug", "switch"),
        ("Speed fan", "switch"),
        ("Bedtime", "scene"),
        ("Film night", "scene"),
    ]
    states = last(client, "states")
    assert states["full"] is True
    assert set(states["items"]) == {item["key"] for item in layout["items"]}


async def test_the_panel_without_an_area_shows_nothing(
    hass: HomeAssistant, area: ar.AreaEntry, init_integration: MockConfigEntry, clients
) -> None:
    layout = last(clients.last, "layout")
    assert layout["title"] == TITLE
    assert layout["items"] == []


async def test_leaving_the_area_empties_the_panel(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    await put_panel_in(hass, None)
    layout = last(clients.last, "layout")
    assert (layout["title"], layout["items"]) == (TITLE, [])


async def test_entities_joining_and_leaving(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory, area
) -> None:
    client = clients.last
    register(hass, "switch.new", area.id)
    await hass.async_block_till_done()
    assert "switch.new" in in_room.runtime_data.layout.waiting  # no state yet
    hass.states.async_set("switch.new", "on", {"friendly_name": "New"})
    await hass.async_block_till_done()
    assert "New" in items_by_name(last(client, "layout"))

    er.async_get(hass).async_update_entity("switch.new", area_id=None)
    await hass.async_block_till_done()
    assert "New" not in items_by_name(last(client, "layout"))


async def test_an_entity_follows_its_device_unless_put_elsewhere(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory, area
) -> None:
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=in_room.entry_id, identifiers={("demo", "strip")}
    )
    entities = er.async_get(hass)
    entities.async_get_or_create(
        "light",
        "demo",
        "strip",
        suggested_object_id="strip",
        device_id=device.id,
        original_name="Strip",
    )
    hass.states.async_set("light.strip", "off", {"friendly_name": "Strip"})
    devices.async_update_device(device.id, area_id=area.id)
    await hass.async_block_till_done()
    assert "Strip" in items_by_name(last(clients.last, "layout"))

    kitchen = ar.async_get(hass).async_create("Kitchen")
    entities.async_update_entity("light.strip", area_id=kitchen.id)
    await hass.async_block_till_done()
    assert "Strip" not in items_by_name(last(clients.last, "layout"))


async def test_renaming_the_area_renames_the_title(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory, area
) -> None:
    ar.async_get(hass).async_update(area.id, name="Lounge")
    await hass.async_block_till_done()
    assert last(clients.last, "layout")["title"] == "Lounge"


async def setup_with(
    hass: HomeAssistant, config_entry: MockConfigEntry, options: dict[str, Any]
) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options=options)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_no_all_lights_item_unless_asked(
    hass: HomeAssistant, area: ar.AreaEntry, config_entry: MockConfigEntry, clients
) -> None:
    await setup_with(hass, config_entry, {})
    await put_panel_in(hass, area.id)
    names = [i["name"] for i in last(clients.last, "layout")["items"]]
    assert names[:2] == ["Ceiling", "Reading lamp"]
    assert "All lights" not in names
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_room_options(
    hass: HomeAssistant, area: ar.AreaEntry, config_entry: MockConfigEntry, clients
) -> None:
    await setup_with(
        hass,
        config_entry,
        {
            CONF_MODE: MODE_ROOM,
            CONF_AREA: area.id,
            "lights": ["light.lamp", "light.ceiling"],
            "switches": ["switch.fan_plug"],
            "sensors": ["sensor.co2"],
            CONF_REMOVED: ["script.bedtime"],
        },
    )
    await put_panel_in(hass, area.id)
    layout = last(clients.last, "layout")
    # The user's order within each kind; the area's other entities after
    # theirs, by name; the one taken off, off.
    assert [i["name"] for i in layout["items"]] == [
        "Reading lamp",
        "Ceiling",
        "Radiator",
        "Window blind",
        "Fan plug",
        "Ceiling fan",
        "Speed fan",
        "Film night",
        "CO2",
    ]
    names = items_by_name(layout)
    assert names["CO2"] == {
        "key": names["CO2"]["key"],
        "kind": "sensor",
        "name": "CO2",
        "icon": "sensor",
        "features": {"unit": "ppm"},
    }

    # A switch added to the room later comes after the listed ones.
    register(hass, "switch.aaa", area.id)
    hass.states.async_set("switch.aaa", "on", {"friendly_name": "Aaa"})
    await hass.async_block_till_done()
    switches = [
        i["name"]
        for i in last(clients.last, "layout")["items"]
        if i["kind"] == "switch"
    ]
    assert switches == ["Fan plug", "Aaa", "Ceiling fan", "Speed fan"]
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_lists_saved_for_another_room(
    hass: HomeAssistant, area: ar.AreaEntry, config_entry: MockConfigEntry, clients
) -> None:
    await setup_with(
        hass,
        config_entry,
        {
            CONF_MODE: MODE_ROOM,
            CONF_AREA: "kitchen",
            "lights": ["light.lamp"],
            "sensors": ["sensor.co2"],
            CONF_REMOVED: ["light.ceiling"],
        },
    )
    await put_panel_in(hass, area.id)
    names = [i["name"] for i in last(clients.last, "layout")["items"]]
    # The living room's own entities, as if nothing were saved; the sensors stay.
    assert names[:2] == ["Ceiling", "Reading lamp"]
    assert names[-1] == "CO2"
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_manual_mode(
    hass: HomeAssistant, area: ar.AreaEntry, config_entry: MockConfigEntry, clients
) -> None:
    hass.states.async_set("media_player.tv", "on")
    hass.states.async_set("light.desk", "on", {"friendly_name": "Desk"})
    await setup_with(
        hass,
        config_entry,
        {
            CONF_MODE: MODE_MANUAL,
            CONF_ALL_LIGHTS: True,
            "lights": ["light.lamp", "light.desk"],
            "switches": [
                "switch.kitchen",
                "media_player.tv",  # no kind for it
                "fan.speeds",  # a switch, on a panel without fans
                "light.ceiling",  # in the wrong list, it still goes with the lights
            ],
            "sensors": ["sensor.co2"],
        },
    )
    await put_panel_in(hass, area.id)  # manual mode ignores the area
    layout = last(clients.last, "layout")
    assert layout["title"] == TITLE
    assert [i["name"] for i in layout["items"]] == [
        "All lights",
        "Reading lamp",
        "Desk",
        "Living room Ceiling",
        "kitchen",
        "Speed fan",
        "CO2",
    ]
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_the_limits(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    clients: ClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    switches = [f"switch.s{n:02}" for n in range(50)]
    for entity_id in switches:
        hass.states.async_set(entity_id, "off", {"friendly_name": "x" * 80})
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_MODE: MODE_MANUAL, "switches": switches}
    )
    caplog.set_level(logging.WARNING)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    items = last(clients.last, "layout")["items"]
    assert len(items) == 48
    assert len(items[0]["name"]) == 64
    assert items[0]["name"].endswith("…")
    assert caplog.text.count("48 items at most") == 1
    hass.states.async_set("switch.s00", "on", {"friendly_name": "x" * 80})
    await hass.async_block_till_done()
    assert caplog.text.count("48 items at most") == 1  # said once
    await hass.config_entries.async_unload(config_entry.entry_id)


# Features and icons ------------------------------------------------------------


async def test_layout_features(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    names = items_by_name(last(clients.last, "layout"))
    assert names["All lights"]["features"] == {"brightness": True}
    assert names["All lights"]["icon"] == "lightbulb-group"
    assert names["Ceiling"]["features"] == {
        "brightness": True,
        "color_temp": {"min_k": 2200, "max_k": 6500},
        "color": True,
        "effects": ["Candle", "Fireplace"],
    }
    assert "features" not in names["Reading lamp"]  # on and off only
    assert names["Radiator"]["features"] == {
        "target": "single",
        "step": 0.5,
        "unit": "°C",
        "hvac_modes": ["off", "heat", "auto"],
        "presets": ["eco", "comfort"],
        "fan_modes": [],
        "min": 5,
        "max": 30,
    }
    assert names["Window blind"]["features"] == {
        "open": True,
        "close": True,
        "stop": True,
        "position": True,
        "tilt": False,
    }
    assert names["Window blind"]["icon"] == "blind"
    assert names["Fan plug"]["icon"] == "power-socket"
    assert names["Ceiling fan"]["icon"] == "fan"
    assert names["Bedtime"]["icon"] == "script"


@pytest.mark.parametrize(
    ("supported", "target"), [(2, "range"), (3, "range"), (1, "single"), (0, "none")]
)
def test_climate_targets(hass: HomeAssistant, supported: int, target: str) -> None:
    features = room.climate_features(
        hass, {"supported_features": supported, "target_temp_step": 1}
    )
    assert features["target"] == target
    assert features["step"] == 1


@pytest.mark.parametrize(
    ("entity_id", "attributes", "icon"),
    [
        ("light.a", {"icon": "mdi:ceiling-light"}, "ceiling-light"),
        ("light.a", {"icon": "mdi:lightbulb-group-off"}, "lightbulb-group"),
        ("light.a", {"icon": "mdi:lightbulb-outline"}, "lightbulb"),
        ("light.a", {"icon": "mdi:floor-lamp-outline"}, "lamp"),
        ("switch.a", {"icon": "mdi:television"}, "tv"),
        ("switch.a", {"icon": "mdi:rocket"}, None),
        ("light.a", {}, None),
        ("sensor.a", {"device_class": "temperature"}, "thermometer"),
        ("cover.a", {"device_class": "garage"}, "garage"),
        ("climate.a", {}, "thermostat"),
    ],
)
def test_icons(entity_id: str, attributes: dict[str, Any], icon: str | None) -> None:
    assert room.icon_for(State(entity_id, "on", attributes)) == icon


async def test_names_lose_only_a_leading_area(hass: HomeAssistant) -> None:
    area = ar.async_get(hass).async_create("Living room")
    assert room._short_name("living room lamp", area) == "Lamp"
    assert room._short_name("Living room", area) == "Living room"
    assert (
        room._short_name("Lamp in the Living room", area) == "Lamp in the Living room"
    )
    assert room._short_name("Living room lamp", None) == "Living room lamp"


# States ------------------------------------------------------------------------


async def test_full_states(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    client = clients.last
    keys = {i["name"]: i["key"] for i in last(client, "layout")["items"]}
    states = {name: last(client, "states")["items"][key] for name, key in keys.items()}
    assert states["Ceiling"] == {
        "available": True,
        "on": True,
        "brightness": 50,
        "color_temp_k": 2700,
        "hs": [30, 51],
        "effect": None,
        "mode": "color_temp",
    }
    assert states["All lights"] == {
        "available": True,
        "on": True,
        "brightness": 50,
        "color_temp_k": None,
        "hs": None,
        "effect": None,
        "mode": "brightness",
    }
    assert states["Reading lamp"]["on"] is False
    assert states["Radiator"] == {
        "available": True,
        "hvac_mode": "heat",
        "hvac_action": "heating",
        "current": 20.5,
        "target": 21,
        "target_low": None,
        "target_high": None,
        "preset": "eco",
        "fan_mode": None,
        "current_humidity": None,
    }
    assert states["Window blind"] == {
        "available": True,
        "state": "open",
        "position": 70,
        "tilt": None,
    }
    assert states["Ceiling fan"] == {"available": True, "on": True}
    assert states["Film night"] == {"available": True}


async def test_only_changes_are_sent(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    client = clients.last
    keys = {i["name"]: i["key"] for i in last(client, "layout")["items"]}
    client.sent.clear()

    hass.states.async_set("light.ceiling", "on", {**LIGHT_ATTRS, "brightness": 255})
    await hass.async_block_till_done()
    assert client.sent == [
        {
            "t": "states",
            "full": False,
            "items": {
                keys["Ceiling"]: {"brightness": 100},
                keys["All lights"]: {"brightness": 100},
            },
        }
    ]

    client.sent.clear()
    hass.states.async_set("light.ceiling", "on", {**LIGHT_ATTRS, "brightness": 255})
    hass.states.async_set(
        "switch.fan_plug", "off", {**ROOM["switch.fan_plug"][1], "x": 1}
    )
    await hass.async_block_till_done()
    assert client.sent == []  # nothing the panel shows changed


async def test_unavailable_and_back(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    client = clients.last
    key = items_by_name(last(client, "layout"))["Fan plug"]["key"]
    attributes = {"friendly_name": "Fan plug", "device_class": "outlet"}
    client.sent.clear()
    hass.states.async_set("switch.fan_plug", "unavailable", attributes)
    await hass.async_block_till_done()
    assert last(client, "states")["items"] == {key: {"available": False}}
    hass.states.async_set("switch.fan_plug", "on", attributes)
    await hass.async_block_till_done()
    assert last(client, "states")["items"] == {key: {"available": True, "on": True}}


async def test_a_renamed_entity_sends_a_new_layout(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    client = clients.last
    before = items_by_name(last(client, "layout"))
    client.sent.clear()
    hass.states.async_set("switch.fan_plug", "off", {"friendly_name": "Heater plug"})
    await hass.async_block_till_done()
    assert [m["t"] for m in client.sent] == ["layout", "states"]
    after = items_by_name(last(client, "layout"))
    # Keys stay with their entities.
    assert after["Heater plug"]["key"] == before["Fan plug"]["key"]
    assert after["Ceiling"]["key"] == before["Ceiling"]["key"]


async def test_changes_wait_for_a_connection(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    client = clients.last
    client.sent.clear()
    client.authenticated = False  # dropped, not yet reported
    hass.states.async_set("switch.fan_plug", "on", {"friendly_name": "Fan plug"})
    await hass.async_block_till_done()
    assert client.sent == []


async def test_a_broken_send_is_left_to_the_connection(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.last.send.side_effect = CannotConnect("broken pipe")
    hass.states.async_set("switch.fan_plug", "on", {"friendly_name": "Fan plug"})
    await hass.async_block_till_done()
    assert in_room.runtime_data.data.connected


@pytest.mark.parametrize(
    ("state", "options", "value"),
    [
        ("612.345", {}, "612.345"),
        ("612.345", {"sensor": {"suggested_display_precision": 1}}, "612.3"),
        ("612.345", {"sensor": {"display_precision": 0}}, "612"),
        ("high", {"sensor": {"display_precision": 0}}, "high"),
        ("unknown", {}, "—"),
    ],
)
async def test_sensor_values(
    hass: HomeAssistant, state: str, options: dict[str, Any], value: str
) -> None:
    entry = register(hass, "sensor.co2", None)
    for domain, domain_options in options.items():
        er.async_get(hass).async_update_entity_options(
            entry.entity_id, domain, domain_options
        )
    assert room.sensor_value(hass, State("sensor.co2", state)) == value


# Intents -----------------------------------------------------------------------


async def intent(
    hass: HomeAssistant, clients: ClientFactory, name: str, action: str, **args: Any
) -> tuple:
    """Send an intent for the item called `name`; return the panel's answer."""
    client = clients.last
    key = items_by_name(last(client, "layout"))[name]["key"] if name != "?" else "zz"
    client.respond.reset_mock()
    message = {"t": "intent", "id": 5, "key": key, "action": action}
    if args:
        message["args"] = args
    client.on_message(message)
    await hass.async_block_till_done(wait_background_tasks=True)
    client.respond.assert_awaited_once()
    return client.respond.await_args.args


@pytest.mark.parametrize(
    ("name", "action", "args", "service", "data"),
    [
        ("Ceiling", "toggle", {}, "light.toggle", {}),
        ("Ceiling", "turn_off", {}, "light.turn_off", {}),
        ("Ceiling", "turn_on", {}, "light.turn_on", {}),
        ("Ceiling", "turn_on", {"brightness": 40}, "light.turn_on", {"brightness_pct": 40}),
        (
            "Ceiling",
            "turn_on",
            {"color_temp_k": 3000},
            "light.turn_on",
            {"color_temp_kelvin": 3000},
        ),
        ("Ceiling", "turn_on", {"hs": [400, 120]}, "light.turn_on", {"hs_color": [40, 100]}),
        ("Ceiling", "turn_on", {"effect": "Candle"}, "light.turn_on", {"effect": "Candle"}),
        ("Fan plug", "turn_on", {}, "switch.turn_on", {}),
        ("Ceiling fan", "toggle", {}, "fan.toggle", {}),
        ("Film night", "activate", {}, "scene.turn_on", {}),
        ("Bedtime", "activate", {}, "script.turn_on", {}),
        ("Radiator", "set_target", {"value": 21.5}, "climate.set_temperature", {"temperature": 21.5}),
        ("Radiator", "set_hvac_mode", {"mode": "off"}, "climate.set_hvac_mode", {"hvac_mode": "off"}),
        ("Radiator", "set_preset", {"preset": "comfort"}, "climate.set_preset_mode", {"preset_mode": "comfort"}),
        ("Window blind", "open", {}, "cover.open_cover", {}),
        ("Window blind", "stop", {}, "cover.stop_cover", {}),
        ("Window blind", "set_position", {"position": 30}, "cover.set_cover_position", {"position": 30}),
    ],
)  # fmt: skip
async def test_intents(
    hass: HomeAssistant,
    in_room: MockConfigEntry,
    clients: ClientFactory,
    name: str,
    action: str,
    args: dict[str, Any],
    service: str,
    data: dict[str, Any],
) -> None:
    domain, service_name = service.split(".")
    calls = async_mock_service(hass, domain, service_name)
    answer = await intent(hass, clients, name, action, **args)
    assert answer == (5,)  # success: no error
    assert len(calls) == 1
    entity_id = next(e for e, (_, a) in ROOM.items() if name in a["friendly_name"])
    assert calls[0].data == {"entity_id": [entity_id], **data}


async def test_all_lights_go_one_way(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    turn_off = async_mock_service(hass, "light", "turn_off")
    turn_on = async_mock_service(hass, "light", "turn_on")
    await intent(hass, clients, "All lights", "toggle")  # the ceiling is on
    assert turn_off[0].data["entity_id"] == ["light.ceiling", "light.lamp"]
    hass.states.async_set("light.ceiling", "off", LIGHT_ATTRS)
    await hass.async_block_till_done()
    await intent(hass, clients, "All lights", "toggle")
    assert turn_on[0].data["entity_id"] == ["light.ceiling", "light.lamp"]
    await intent(hass, clients, "All lights", "turn_on", brightness=10)
    assert turn_on[1].data == {
        "entity_id": ["light.ceiling", "light.lamp"],
        "brightness_pct": 10,
    }


@pytest.mark.parametrize(
    ("name", "action", "args", "code"),
    [
        ("?", "toggle", {}, "unknown_key"),
        ("Reading lamp", "turn_on", {"brightness": 40}, "unsupported"),
        ("Ceiling", "turn_on", {"effect": "Disco"}, "unsupported"),
        ("Ceiling", "turn_on", {"brightness": 40, "effect": "Candle"}, "bad_request"),
        ("Ceiling", "turn_on", {"brightness": "lots"}, "bad_request"),
        ("Ceiling", "turn_on", {"sparkle": 1}, "bad_request"),
        ("Ceiling", "blink", {}, "unsupported"),
        ("Fan plug", "activate", {}, "unsupported"),
        ("Film night", "toggle", {}, "unsupported"),
        ("Radiator", "set_range", {"low": 18, "high": 22}, "unsupported"),
        ("Radiator", "set_hvac_mode", {"mode": "cool"}, "unsupported"),
        ("Radiator", "set_fan_mode", {"fan_mode": "high"}, "unsupported"),
        ("Radiator", "set_target", {"value": "warm"}, "bad_request"),
        ("Window blind", "set_tilt", {"tilt": 10}, "unsupported"),
        ("Window blind", "set_position", {"position": 140}, "bad_request"),
    ],
)
async def test_intents_refused(
    hass: HomeAssistant,
    in_room: MockConfigEntry,
    clients: ClientFactory,
    name: str,
    action: str,
    args: dict[str, Any],
    code: str,
) -> None:
    answer = await intent(hass, clients, name, action, **args)
    assert answer[:2] == (5, code)


async def test_intent_for_a_sensor(
    hass: HomeAssistant, area, config_entry: MockConfigEntry, clients: ClientFactory
) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_MODE: MODE_MANUAL, "sensors": ["sensor.co2"]}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    answer = await intent(hass, clients, "CO2", "toggle")
    assert answer[:2] == (5, "unsupported")
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_intent_without_an_action(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    key = items_by_name(last(clients.last, "layout"))["Ceiling"]["key"]
    clients.last.on_message({"t": "intent", "id": 5, "key": key})
    await hass.async_block_till_done()
    assert clients.last.respond.await_args.args[:2] == (5, "bad_request")


async def test_intent_for_an_unavailable_entity(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    hass.states.async_set(
        "switch.fan_plug", "unavailable", {"friendly_name": "Fan plug"}
    )
    await hass.async_block_till_done()
    answer = await intent(hass, clients, "Fan plug", "toggle")
    assert answer[:2] == (5, "unavailable")


async def test_a_failing_service(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    async def fail(_call: ServiceCall) -> None:
        raise HomeAssistantError("the bulb is on fire")

    hass.services.async_register("switch", "toggle", fail)
    answer = await intent(hass, clients, "Fan plug", "toggle")
    assert answer == (5, "service_failed", "the bulb is on fire")


async def test_a_missing_service(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    # The panel's own switches load the switch integration; take its toggle away.
    hass.services.async_remove("switch", "toggle")
    answer = await intent(hass, clients, "Fan plug", "toggle")
    assert answer[:2] == (5, "service_failed")


async def test_a_slow_service(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    release = asyncio.Event()

    async def slow(_call: ServiceCall) -> None:
        await release.wait()

    hass.services.async_register("switch", "toggle", slow)
    with patch.object(room, "INTENT_TIMEOUT", 0.01):
        answer = await intent(hass, clients, "Fan plug", "toggle")
    release.set()
    assert answer == (5, "service_failed", "switch.toggle timed out")


async def test_an_intent_before_any_layout(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    init_integration.runtime_data.layout = None
    clients.last.on_message({"t": "intent", "id": 5, "key": "e1", "action": "toggle"})
    await hass.async_block_till_done()
    assert clients.last.respond.await_args.args[:2] == (5, "unknown_key")


async def test_an_answer_that_cant_be_sent(
    hass: HomeAssistant, in_room: MockConfigEntry, clients: ClientFactory
) -> None:
    async_mock_service(hass, "switch", "toggle")
    clients.last.respond.side_effect = CannotConnect("closed")
    key = items_by_name(last(clients.last, "layout"))["Fan plug"]["key"]
    clients.last.on_message({"t": "intent", "id": 5, "key": key, "action": "toggle"})
    await hass.async_block_till_done()
    assert in_room.runtime_data.data.connected


# Fans --------------------------------------------------------------------------

KINDS = ["light", "switch", "scene", "climate", "cover", "sensor", "fan"]
BEDROOM_FAN = {
    "friendly_name": "Bedroom fan",
    "supported_features": 1 | 2 | 4 | 8 | 16 | 32,
    "percentage": 67,
    "percentage_step": 100 / 3,
    "preset_modes": ["breeze", "sleep"],
    "preset_mode": None,
    "oscillating": False,
    "direction": "forward",
}
FANS = {
    "Bedroom fan": "fan.bedroom",
    "Ceiling fan": "fan.simple",
    "Speed fan": "fan.speeds",
}


@pytest.fixture
async def fans(
    hass: HomeAssistant, area: ar.AreaEntry, config_entry: MockConfigEntry, clients
) -> MockConfigEntry:
    """A panel that draws fans, showing three among a light, a switch and a scene."""
    clients.hello = make_hello(
        caps={"beeper": True, "input": ["ring", "press"], "kinds": KINDS}
    )
    hass.states.async_set("fan.bedroom", "on", BEDROOM_FAN)
    await setup_with(
        hass,
        config_entry,
        {
            CONF_MODE: MODE_MANUAL,
            "lights": ["light.lamp"],
            "switches": ["fan.bedroom", "switch.fan_plug", "fan.simple", "fan.speeds"],
            "scenes": ["scene.film"],
        },
    )
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_fans_on_a_panel_that_draws_them(
    hass: HomeAssistant, fans: MockConfigEntry, clients: ClientFactory
) -> None:
    client = clients.last
    layout = last(client, "layout")
    # Among the switches, in the user's order.
    assert [(i["name"], i["kind"]) for i in layout["items"]] == [
        ("Reading lamp", "light"),
        ("Bedroom fan", "fan"),
        ("Fan plug", "switch"),
        ("Ceiling fan", "fan"),
        ("Speed fan", "fan"),
        ("Film night", "scene"),
    ]
    names = items_by_name(layout)
    assert names["Bedroom fan"]["icon"] == "fan"
    assert names["Bedroom fan"]["features"] == {
        "speed": True,
        "speed_step": 33.33,
        "presets": ["breeze", "sleep"],
        "oscillate": True,
        "direction": True,
    }
    assert names["Ceiling fan"]["features"] == {
        "speed": False,
        "presets": [],
        "oscillate": False,
        "direction": False,
    }
    # A fan with speeds that doesn't say how far apart: every percent.
    assert names["Speed fan"]["features"]["speed_step"] == 1

    states = last(client, "states")["items"]
    assert states[names["Bedroom fan"]["key"]] == {
        "available": True,
        "on": True,
        "percentage": 67,
        "preset": None,
        "oscillating": False,
        "direction": "forward",
    }
    assert states[names["Ceiling fan"]["key"]] == {
        "available": True,
        "on": True,
        "percentage": None,
        "preset": None,
        "oscillating": False,
        "direction": None,
    }

    hass.states.async_set(
        "fan.bedroom",
        "on",
        {
            **BEDROOM_FAN,
            "preset_mode": "sleep",
            "oscillating": True,
            "direction": "reverse",
        },
    )
    await hass.async_block_till_done()
    assert last(client, "states")["items"] == {
        names["Bedroom fan"]["key"]: {
            "preset": "sleep",
            "oscillating": True,
            "direction": "reverse",
        }
    }


@pytest.mark.parametrize(
    ("name", "action", "args", "service", "data"),
    [
        ("Bedroom fan", "toggle", {}, "fan.toggle", {}),
        ("Bedroom fan", "turn_off", {}, "fan.turn_off", {}),
        ("Ceiling fan", "turn_on", {}, "fan.turn_on", {}),
        ("Bedroom fan", "set_percentage", {"percentage": 33}, "fan.set_percentage", {"percentage": 33}),
        ("Bedroom fan", "set_percentage", {"percentage": 0}, "fan.set_percentage", {"percentage": 0}),
        ("Bedroom fan", "set_preset", {"preset": "sleep"}, "fan.set_preset_mode", {"preset_mode": "sleep"}),
        ("Bedroom fan", "oscillate", {"oscillating": True}, "fan.oscillate", {"oscillating": True}),
        ("Bedroom fan", "set_direction", {"direction": "reverse"}, "fan.set_direction", {"direction": "reverse"}),
    ],
)  # fmt: skip
async def test_fan_intents(
    hass: HomeAssistant,
    fans: MockConfigEntry,
    clients: ClientFactory,
    name: str,
    action: str,
    args: dict[str, Any],
    service: str,
    data: dict[str, Any],
) -> None:
    domain, service_name = service.split(".")
    calls = async_mock_service(hass, domain, service_name)
    answer = await intent(hass, clients, name, action, **args)
    assert answer == (5,)
    assert len(calls) == 1
    assert calls[0].data == {"entity_id": [FANS[name]], **data}


@pytest.mark.parametrize(
    ("name", "action", "args", "code"),
    [
        ("Ceiling fan", "set_percentage", {"percentage": 50}, "unsupported"),
        ("Ceiling fan", "oscillate", {"oscillating": True}, "unsupported"),
        ("Ceiling fan", "set_direction", {"direction": "forward"}, "unsupported"),
        ("Ceiling fan", "set_preset", {"preset": "breeze"}, "unsupported"),
        ("Bedroom fan", "set_preset", {"preset": "turbo"}, "unsupported"),
        ("Bedroom fan", "activate", {}, "unsupported"),
        ("Bedroom fan", "set_percentage", {"percentage": 140}, "bad_request"),
        ("Bedroom fan", "set_percentage", {}, "bad_request"),
        ("Bedroom fan", "oscillate", {"oscillating": "yes"}, "bad_request"),
        ("Bedroom fan", "set_direction", {"direction": "up"}, "bad_request"),
    ],
)
async def test_fan_intents_refused(
    hass: HomeAssistant,
    fans: MockConfigEntry,
    clients: ClientFactory,
    name: str,
    action: str,
    args: dict[str, Any],
    code: str,
) -> None:
    answer = await intent(hass, clients, name, action, **args)
    assert answer[:2] == (5, code)


# Locks -------------------------------------------------------------------------

LOCKS = {
    "lock.front": ("locked", {"friendly_name": "Front door", "supported_features": 1}),
    "lock.back": ("unlocked", {"friendly_name": "Back door", "supported_features": 0}),
    "lock.gate": ("unknown", {"friendly_name": "Gate"}),
    # Needs a code: shown only with a default code in its entity settings.
    "lock.shed": ("locked", {"friendly_name": "Shed", "code_format": r"^\d{4}$"}),
    "lock.safe": ("locked", {"friendly_name": "Safe", "code_format": r"^\d{4}$"}),
}
LOCK_OPTIONS = {
    CONF_MODE: MODE_MANUAL,
    "lights": ["light.lamp"],
    "covers": ["cover.blind"],
    "locks": list(LOCKS),
    "switches": ["fan.simple", "switch.fan_plug"],
}


@pytest.fixture
def lock_states(hass: HomeAssistant, area: ar.AreaEntry) -> None:
    register(hass, "lock.shed", None)
    er.async_get(hass).async_update_entity_options(
        "lock.shed", "lock", {"default_code": "1234"}
    )
    for entity_id, (state, attributes) in LOCKS.items():
        hass.states.async_set(entity_id, state, attributes)


@pytest.fixture
async def locks(
    hass: HomeAssistant, lock_states: None, config_entry: MockConfigEntry, clients
) -> MockConfigEntry:
    """A panel that draws locks and fans, showing locks among other kinds."""
    clients.hello = make_hello(
        caps={"beeper": True, "input": ["ring", "press"], "kinds": [*KINDS, "lock"]}
    )
    await setup_with(hass, config_entry, LOCK_OPTIONS)
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_locks_on_a_panel_that_draws_them(
    hass: HomeAssistant, locks: MockConfigEntry, clients: ClientFactory
) -> None:
    client = clients.last
    layout = last(client, "layout")
    # After the covers, before the switches; the safe needs a code it hasn't got.
    assert [(i["name"], i["kind"]) for i in layout["items"]] == [
        ("Reading lamp", "light"),
        ("Window blind", "cover"),
        ("Front door", "lock"),
        ("Back door", "lock"),
        ("Gate", "lock"),
        ("Shed", "lock"),
        ("Ceiling fan", "fan"),
        ("Fan plug", "switch"),
    ]
    names = items_by_name(layout)
    # No icon: the panel shows a lock, open or not, as its state says.
    assert names["Front door"] == {
        "key": names["Front door"]["key"],
        "kind": "lock",
        "name": "Front door",
        "features": {"open": True},
    }
    assert names["Back door"]["features"] == {"open": False}

    states = last(client, "states")["items"]
    assert states[names["Front door"]["key"]] == {"available": True, "state": "locked"}
    assert states[names["Back door"]["key"]] == {"available": True, "state": "unlocked"}
    assert states[names["Gate"]["key"]] == {"available": True, "state": None}

    hass.states.async_set("lock.front", "jammed", LOCKS["lock.front"][1])
    await hass.async_block_till_done()
    assert last(client, "states")["items"] == {
        names["Front door"]["key"]: {"state": "jammed"}
    }
    hass.states.async_set("lock.front", "unavailable", LOCKS["lock.front"][1])
    await hass.async_block_till_done()
    assert last(client, "states")["items"] == {
        names["Front door"]["key"]: {"available": False}
    }


async def test_locks_are_left_out_on_an_older_panel(
    hass: HomeAssistant, lock_states: None, config_entry: MockConfigEntry, clients
) -> None:
    await setup_with(hass, config_entry, LOCK_OPTIONS)
    layout = last(clients.last, "layout")
    assert [i["name"] for i in layout["items"]] == [
        "Reading lamp",
        "Window blind",
        "Ceiling fan",
        "Fan plug",
    ]
    await hass.config_entries.async_unload(config_entry.entry_id)


@pytest.mark.parametrize(
    ("name", "action", "service"),
    [
        ("Front door", "lock", "lock.lock"),
        ("Front door", "unlock", "lock.unlock"),
        ("Front door", "open", "lock.open"),
        ("Shed", "unlock", "lock.unlock"),
    ],
)
async def test_lock_intents(
    hass: HomeAssistant,
    locks: MockConfigEntry,
    clients: ClientFactory,
    name: str,
    action: str,
    service: str,
) -> None:
    domain, service_name = service.split(".")
    calls = async_mock_service(hass, domain, service_name)
    answer = await intent(hass, clients, name, action)
    assert answer == (5,)
    assert len(calls) == 1
    # No code: Home Assistant fills in the shed's default one.
    entity_id = next(e for e, (_, a) in LOCKS.items() if a["friendly_name"] == name)
    assert calls[0].data == {"entity_id": [entity_id]}


@pytest.mark.parametrize(
    ("name", "action"),
    [("Back door", "open"), ("Front door", "toggle"), ("Front door", "turn_on")],
)
async def test_lock_intents_refused(
    hass: HomeAssistant,
    locks: MockConfigEntry,
    clients: ClientFactory,
    name: str,
    action: str,
) -> None:
    answer = await intent(hass, clients, name, action)
    assert answer[:2] == (5, "unsupported")


# Media players -----------------------------------------------------------------

SPEAKER = {
    "friendly_name": "Speaker",
    # Every feature the panel uses: volume, mute, play and pause, tracks,
    # sources, on and off.
    "supported_features": 4 | 8 | 16384 | 1 | 32 | 16 | 2048 | 128 | 256,
    "volume_level": 0.305,
    "is_volume_muted": False,
    "media_title": "Morning tide",
    "media_artist": "The Keepers",
    "source": "Radio",
    "source_list": ["Radio", "Turntable"],
}
MEDIA = {
    "media_player.speaker": ("paused", SPEAKER),
    # Off, with only power and a source list it can't select from.
    "media_player.tv": (
        "off",
        {
            "friendly_name": "TV",
            "device_class": "tv",
            "supported_features": 128 | 256,
            "source_list": ["HDMI 1"],
        },
    ),
    # Play without pause, next without previous: neither control.
    "media_player.cast": (
        "unknown",
        {"friendly_name": "Cast", "supported_features": 16384 | 32},
    ),
}
MEDIA_OPTIONS = {
    CONF_MODE: MODE_MANUAL,
    "lights": ["light.lamp"],
    "switches": ["switch.fan_plug"],
    "media_players": list(MEDIA),
}


@pytest.fixture
def media_states(hass: HomeAssistant, area: ar.AreaEntry) -> None:
    for entity_id, (state, attributes) in MEDIA.items():
        hass.states.async_set(entity_id, state, attributes)


@pytest.fixture
async def media(
    hass: HomeAssistant, media_states: None, config_entry: MockConfigEntry, clients
) -> MockConfigEntry:
    """A panel that draws media players, showing them among other kinds."""
    clients.hello = make_hello(
        caps={
            "beeper": True,
            "input": ["ring", "press"],
            "kinds": [*KINDS, "media_player"],
        }
    )
    await setup_with(hass, config_entry, MEDIA_OPTIONS)
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_media_players_on_a_panel_that_draws_them(
    hass: HomeAssistant, media: MockConfigEntry, clients: ClientFactory
) -> None:
    client = clients.last
    layout = last(client, "layout")
    # After the switches.
    assert [(i["name"], i["kind"]) for i in layout["items"]] == [
        ("Reading lamp", "light"),
        ("Fan plug", "switch"),
        ("Speaker", "media_player"),
        ("TV", "media_player"),
        ("Cast", "media_player"),
    ]
    names = items_by_name(layout)
    # No icon but the TV's: the panel's default is a speaker.
    assert names["Speaker"] == {
        "key": names["Speaker"]["key"],
        "kind": "media_player",
        "name": "Speaker",
        "features": {
            "volume": True,
            "mute": True,
            "play_pause": True,
            "tracks": True,
            "power": True,
            "sources": ["Radio", "Turntable"],
        },
    }
    assert names["TV"]["icon"] == "tv"
    assert names["TV"]["features"] == {
        "volume": False,
        "mute": False,
        "play_pause": False,
        "tracks": False,
        "power": True,
    }
    assert not any(names["Cast"]["features"].values())

    states = last(client, "states")["items"]
    assert states[names["Speaker"]["key"]] == {
        "available": True,
        "state": "paused",
        "volume": 30,
        "muted": False,
        "title": "Morning tide",
        "artist": "The Keepers",
        "source": "Radio",
    }
    assert states[names["TV"]["key"]] == {
        "available": True,
        "state": "off",
        "volume": None,
        "muted": False,
        "title": None,
        "artist": None,
        "source": None,
    }
    assert states[names["Cast"]["key"]]["state"] is None

    hass.states.async_set(
        "media_player.speaker",
        "playing",
        SPEAKER | {"media_title": "Second song", "volume_level": 0.5},
    )
    await hass.async_block_till_done()
    assert last(client, "states")["items"] == {
        names["Speaker"]["key"]: {
            "state": "playing",
            "title": "Second song",
            "volume": 50,
        }
    }


async def test_media_players_are_left_out_on_an_older_panel(
    hass: HomeAssistant, media_states: None, config_entry: MockConfigEntry, clients
) -> None:
    await setup_with(hass, config_entry, MEDIA_OPTIONS)
    layout = last(clients.last, "layout")
    assert [i["name"] for i in layout["items"]] == ["Reading lamp", "Fan plug"]
    await hass.config_entries.async_unload(config_entry.entry_id)


@pytest.mark.parametrize(
    ("action", "args", "service", "data"),
    [
        ("set_volume", {"volume": 35}, "volume_set", {"volume_level": 0.35}),
        ("mute", {"muted": True}, "volume_mute", {"is_volume_muted": True}),
        ("play", {}, "media_play", {}),
        ("pause", {}, "media_pause", {}),
        ("next", {}, "media_next_track", {}),
        ("previous", {}, "media_previous_track", {}),
        (
            "select_source",
            {"source": "Turntable"},
            "select_source",
            {"source": "Turntable"},
        ),
        ("turn_on", {}, "turn_on", {}),
        ("turn_off", {}, "turn_off", {}),
    ],
)
async def test_media_intents(
    hass: HomeAssistant,
    media: MockConfigEntry,
    clients: ClientFactory,
    action: str,
    args: dict[str, Any],
    service: str,
    data: dict[str, Any],
) -> None:
    calls = async_mock_service(hass, "media_player", service)
    answer = await intent(hass, clients, "Speaker", action, **args)
    assert answer == (5,)
    assert len(calls) == 1
    assert calls[0].data == {"entity_id": ["media_player.speaker"], **data}


@pytest.mark.parametrize(
    ("name", "action", "args", "code"),
    [
        ("TV", "set_volume", {"volume": 20}, "unsupported"),
        ("TV", "play", {}, "unsupported"),
        ("TV", "select_source", {"source": "HDMI 1"}, "unsupported"),
        ("Cast", "play", {}, "unsupported"),
        ("Cast", "next", {}, "unsupported"),
        ("Speaker", "select_source", {"source": "Tape"}, "unsupported"),
        ("Speaker", "toggle", {}, "unsupported"),
        ("Speaker", "set_volume", {"volume": 140}, "bad_request"),
        ("Speaker", "set_volume", {}, "bad_request"),
        ("Speaker", "mute", {"muted": "yes"}, "bad_request"),
    ],
)
async def test_media_intents_refused(
    hass: HomeAssistant,
    media: MockConfigEntry,
    clients: ClientFactory,
    name: str,
    action: str,
    args: dict[str, Any],
    code: str,
) -> None:
    answer = await intent(hass, clients, name, action, **args)
    assert answer[:2] == (5, code)
