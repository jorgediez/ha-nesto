"""What the panel shows: Home Assistant entities as panel items.

PROTOCOL.md appendix A describes the mapping; sections 9 to 11 the messages.
The options keep one ordered list of entities per section (lights, climate,
covers, locks, switches, scenes, sensors), which the panel shows in that
order. In
room mode the lists start as the area the panel's own device is in, so
choosing the panel's area in Home Assistant chooses its room, and they follow
the area: entities added to it later come after the user's, by name, and the
ones the user took off stay off. In manual mode the lists are all there is.
Either way each entity becomes one item, of a kind the panel knows, and the
panel's intents become service calls.

Nothing here talks to the panel: the coordinator sends what this module
builds, and answers intents with what it returns.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
import logging
from typing import Any, Final

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_ICON,
    ATTR_SUPPORTED_FEATURES,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, HomeAssistant, State, split_entity_id
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from .const import (
    CONF_ALL_LIGHTS,
    CONF_AREA,
    CONF_MODE,
    CONF_REMOVED,
    DOMAIN,
    INTENT_TIMEOUT,
    MODE_MANUAL,
)

_LOGGER = logging.getLogger(__name__)

# Section 21.
MAX_ITEMS: Final = 48
MAX_NAME: Final = 64
MAX_OPTIONS: Final = 32

ALL_LIGHTS_KEY: Final = "all"

# The kind each supported domain becomes, and the options section it's in. A
# fan is a `fan` on a panel that draws them (`caps.kinds`), in the switches.
DOMAIN_KINDS: Final = {
    "light": "light",
    "switch": "switch",
    "input_boolean": "switch",
    "fan": "switch",
    "scene": "scene",
    "script": "scene",
    "climate": "climate",
    "cover": "cover",
    "lock": "lock",
    "sensor": "sensor",
}
# What room mode takes from an area: sensors only when the user picks them.
ROOM_DOMAINS: Final = frozenset(DOMAIN_KINDS) - {"sensor"}
# Appendix A's order: synthetic "all lights", then these.
KIND_ORDER: Final = ("light", "climate", "cover", "lock", "switch", "scene", "sensor")
# The options' sections, one per kind, in that order.
SECTIONS: Final = {
    "lights": "light",
    "climate": "climate",
    "covers": "cover",
    "locks": "lock",
    "switches": "switch",
    "scenes": "scene",
    "sensors": "sensor",
}
SECTION_OF_KIND: Final = {kind: section for section, kind in SECTIONS.items()}
# Kinds that newer panels draw, and the kind an older one gets instead.
FALLBACK_KINDS: Final = {"fan": "switch"}
# Kinds that newer panels draw, left out on an older one.
NEW_KINDS: Final = frozenset({"lock"})


def section_domains(section: str) -> list[str]:
    """Return the domains a section's entities can be in."""
    return sorted(d for d, kind in DOMAIN_KINDS.items() if kind == SECTIONS[section])


# FanEntityFeature.
FAN_SET_SPEED, FAN_OSCILLATE, FAN_DIRECTION, FAN_PRESET_MODE = 1, 2, 4, 8
FAN_DIRECTIONS: Final = ("forward", "reverse")
LOCK_OPEN: Final = 1  # LockEntityFeature.OPEN
LOCK_STATES: Final = frozenset(
    {"locked", "unlocked", "locking", "unlocking", "jammed", "open", "opening"}
)
CLIMATE_TARGET: Final = 1  # ClimateEntityFeature.TARGET_TEMPERATURE
CLIMATE_RANGE: Final = 2  # ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
COVER_OPEN, COVER_CLOSE, COVER_POSITION, COVER_STOP = 1, 2, 4, 8
COVER_TILT_POSITION: Final = 128

COLOR_MODES: Final = frozenset({"hs", "xy", "rgb", "rgbw", "rgbww"})
NO_EFFECTS: Final = frozenset({"off", "none"})

# Appendix C: the panel's built-in icons.
ICONS: Final = frozenset(
    {
        "lightbulb", "lightbulb-group", "ceiling-light", "lamp", "led-strip",
        "switch", "power-socket", "fan", "scene", "script", "thermostat",
        "radiator", "air-conditioner", "blind", "shutter", "curtains",
        "garage", "window", "door", "sensor", "thermometer", "water", "tv",
        "speaker", "bell", "alert", "info",
    }
)  # fmt: skip
# Material Design names whose closest built-in icon isn't a prefix of them.
ICON_ALIASES: Final = {
    "floor-lamp": "lamp",
    "desk-lamp": "lamp",
    "power-plug": "power-socket",
    "television": "tv",
    "blinds": "blind",
    "roller-shade": "blind",
    "window-shutter": "shutter",
    "thermostat-box": "thermostat",
    "water-percent": "water",
    "home-thermometer": "thermometer",
}
# Built-in icons longest first, so "lightbulb-group-off" finds the group.
ICON_PREFIXES: Final = sorted(ICONS, key=len, reverse=True)
DEVICE_CLASS_ICONS: Final = {
    ("switch", "outlet"): "power-socket",
    ("cover", "blind"): "blind",
    ("cover", "shade"): "blind",
    ("cover", "shutter"): "shutter",
    ("cover", "curtain"): "curtains",
    ("cover", "garage"): "garage",
    ("cover", "gate"): "garage",
    ("cover", "window"): "window",
    ("cover", "door"): "door",
    ("sensor", "temperature"): "thermometer",
    ("sensor", "humidity"): "water",
}
DOMAIN_ICONS: Final = {
    "fan": "fan",
    "scene": "scene",
    "script": "script",
    "climate": "thermostat",
    "sensor": "sensor",
}


class IntentError(Exception):
    """An intent that can't be carried out; `code` is section 20's."""

    def __init__(self, code: str, message: str) -> None:
        """Keep the error code for the panel's answer."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Item:
    """One thing on the panel: an entity, or every light in the room."""

    key: str
    kind: str
    name: str
    entity_ids: tuple[str, ...]
    features: Mapping[str, Any] = field(default_factory=dict)
    icon: str | None = None

    @property
    def entity_id(self) -> str:
        """Return the one entity this item controls, or the first light."""
        return self.entity_ids[0]

    @property
    def domain(self) -> str:
        """Return the domain of the entities."""
        return split_entity_id(self.entity_id)[0]

    def as_layout(self) -> dict[str, Any]:
        """Return the item as section 9 sends it."""
        layout: dict[str, Any] = {"key": self.key, "kind": self.kind, "name": self.name}
        if self.icon:
            layout["icon"] = self.icon
        if self.features:
            layout["features"] = dict(self.features)
        return layout


@dataclass(frozen=True)
class Layout:
    """The panel's title and items, in display order."""

    title: str
    items: tuple[Item, ...] = ()
    # Entities the room would show if they had a state, or fewer items.
    waiting: frozenset[str] = frozenset()
    left_out: tuple[str, ...] = ()

    def item(self, key: str) -> Item | None:
        """Return the item with this key."""
        return next((item for item in self.items if item.key == key), None)

    def as_message(self, rev: int) -> dict[str, Any]:
        """Return the `layout` message (section 9)."""
        return {
            "t": "layout",
            "rev": rev,
            "title": self.title,
            "items": [item.as_layout() for item in self.items],
        }

    @property
    def entity_ids(self) -> set[str]:
        """Return every entity the items show."""
        return {entity_id for item in self.items for entity_id in item.entity_ids}


class Keys:
    """Short item keys that stay with their entities.

    A key keeps meaning the same entity for as long as Home Assistant runs,
    so an intent sent just before a layout change can't reach another entity:
    it either finds its entity or gets `unknown_key`.
    """

    def __init__(self) -> None:
        """Start with no keys handed out."""
        self._keys: dict[str, str] = {}

    def __call__(self, entity_id: str) -> str:
        """Return the key for an entity, handing out a new one if needed."""
        if entity_id not in self._keys:
            self._keys[entity_id] = f"e{len(self._keys) + 1}"
        return self._keys[entity_id]


# Choosing the entities ---------------------------------------------------------


def panel_area(
    hass: HomeAssistant, entry_id: str, panel_id: str
) -> ar.AreaEntry | None:
    """Return the area the panel's own device is in."""
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, panel_id), entry_id
    )
    if device is None or device.area_id is None:
        return None
    return ar.async_get(hass).async_get_area(device.area_id)


def area_entities(hass: HomeAssistant, area_id: str) -> list[str]:
    """Return the room's entities, as Home Assistant's area cards choose them.

    An entity is in the area if it was put there, or if its device is there
    and the entity wasn't put elsewhere. Hidden, disabled, configuration and
    diagnostic entities aren't controls for a room, so they're left out.
    """
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    found: dict[str, er.RegistryEntry] = {
        entry.entity_id: entry for entry in er.async_entries_for_area(entities, area_id)
    }
    for device in dr.async_entries_for_area(devices, area_id):
        for entry in er.async_entries_for_device(entities, device.id):
            if entry.area_id is None:
                found.setdefault(entry.entity_id, entry)
    return [
        entity_id
        for entity_id, entry in found.items()
        if entry.disabled_by is None
        and entry.hidden_by is None
        and entry.entity_category is None
        and entry.platform != DOMAIN
        and split_entity_id(entity_id)[0] in ROOM_DOMAINS
    ]


def section_lists(
    hass: HomeAssistant, options: Mapping[str, Any], area: ar.AreaEntry | None
) -> dict[str, list[str]]:
    """Return each section's entities, in the order the panel shows them.

    In manual mode, the lists as saved. In room mode, the lists saved for
    this area, then the area's entities that are in none of them and weren't
    taken off, by name. Lists saved for another area (the panel moved) give
    way to the new area's entities; the sensors, which the user always picks,
    stay.
    """
    saved = {
        section: list(dict.fromkeys(options.get(section, []))) for section in SECTIONS
    }
    if options.get(CONF_MODE) == MODE_MANUAL:
        return saved
    area_id = area.id if area is not None else None
    removed: set[str] = set()
    if CONF_AREA in options and options[CONF_AREA] == area_id:
        removed = set(options.get(CONF_REMOVED, []))
    else:
        saved = {section: [] for section in SECTIONS} | {"sensors": saved["sensors"]}
    if area is None:
        return saved
    listed = {entity_id for entities in saved.values() for entity_id in entities}
    added = [
        entity_id
        for entity_id in area_entities(hass, area.id)
        if entity_id not in listed and entity_id not in removed
    ]
    added.sort(key=lambda entity_id: _sort_name(hass, entity_id))
    for entity_id in added:
        kind = DOMAIN_KINDS[split_entity_id(entity_id)[0]]
        saved[SECTION_OF_KIND[kind]].append(entity_id)
    return saved


def removed_from_area(
    hass: HomeAssistant, area: ar.AreaEntry | None, lists: Mapping[str, list[str]]
) -> list[str]:
    """Return the area's entities that none of the lists keep."""
    if area is None:
        return []
    listed = {entity_id for entities in lists.values() for entity_id in entities}
    return [e for e in area_entities(hass, area.id) if e not in listed]


def _sort_name(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    return (state.name if state is not None else entity_id).casefold()


# Building the layout -----------------------------------------------------------


def build_layout(
    hass: HomeAssistant,
    entry: ConfigEntry,
    panel_id: str,
    keys: Keys,
    kinds: Collection[str] = (),
) -> Layout:
    """Return what the panel shows now.

    `kinds` is what the panel's hello lists in `caps.kinds`: a kind it
    doesn't list becomes its fallback (appendix A).
    """
    options = entry.options
    title = entry.title
    area = (
        None
        if options.get(CONF_MODE) == MODE_MANUAL
        else panel_area(hass, entry.entry_id, panel_id)
    )
    if area is not None:
        title = area.name
    items: list[Item] = []
    waiting: set[str] = set()
    lists = section_lists(hass, options, area)
    chosen = dict.fromkeys(e for section in SECTIONS for e in lists[section])
    for entity_id in chosen:
        state = hass.states.get(entity_id)
        if state is None:
            waiting.add(entity_id)
            continue
        item = entity_item(hass, state, keys(entity_id), area, kinds)
        if item is not None:
            items.append(item)

    # The lists are in kind order already; this only keeps an entity saved in
    # the wrong section with its kind. The sort is stable, so the order within
    # a kind is the user's; fans stay among the switches.
    items.sort(
        key=lambda item: KIND_ORDER.index(FALLBACK_KINDS.get(item.kind, item.kind))
    )
    lights = [item for item in items if item.kind == "light"]
    if options.get(CONF_ALL_LIGHTS, False) and len(lights) >= 2:
        items.insert(0, all_lights(lights))
    left_out = tuple(item.entity_id for item in items[MAX_ITEMS:])
    return Layout(
        title=_clip(title),
        items=tuple(items[:MAX_ITEMS]),
        waiting=frozenset(waiting),
        left_out=left_out,
    )


def entity_item(
    hass: HomeAssistant,
    state: State,
    key: str,
    area: ar.AreaEntry | None,
    kinds: Collection[str] = (),
) -> Item | None:
    """Return the item an entity becomes, or None if no kind fits."""
    domain = state.domain
    kind = DOMAIN_KINDS.get(domain)
    attributes = state.attributes
    if kind is None or (kind in NEW_KINDS and kind not in kinds):
        return None
    if domain in FALLBACK_KINDS and domain in kinds:
        kind = domain
    if (
        kind == "lock"
        and attributes.get("code_format")
        and not _default_code(hass, state.entity_id)
    ):
        # The panel never asks for a code: Home Assistant fills in its default.
        return None
    features: dict[str, Any]
    if kind == "light":
        features = light_features(attributes)
    elif kind == "climate":
        features = climate_features(hass, attributes)
    elif kind == "cover":
        features = cover_features(attributes)
    elif kind == "fan":
        features = fan_features(attributes)
    elif kind == "lock":
        supported = attributes.get(ATTR_SUPPORTED_FEATURES, 0)
        features = {"open": bool(supported & LOCK_OPEN)}
    elif kind == "sensor":
        unit = attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        features = {"unit": unit} if unit else {}
    else:
        features = {}
    return Item(
        key=key,
        kind=kind,
        name=_clip(_short_name(state.name, area)),
        entity_ids=(state.entity_id,),
        features=features,
        # A lock's icon follows its state on the panel: lock, or lock-open.
        icon=None if kind == "lock" else icon_for(state),
    )


def _default_code(hass: HomeAssistant, entity_id: str) -> bool:
    """Return whether a lock has a default code in its entity settings."""
    entry = er.async_get(hass).async_get(entity_id)
    return bool(entry and entry.options.get("lock", {}).get("default_code"))


def all_lights(lights: list[Item]) -> Item:
    """Return the synthetic item that controls every light in the room."""
    brightness = any(light.features.get("brightness") for light in lights)
    return Item(
        key=ALL_LIGHTS_KEY,
        kind="light",
        name="All lights",
        entity_ids=tuple(light.entity_id for light in lights),
        features={"brightness": True} if brightness else {},
        icon="lightbulb-group",
    )


def light_features(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Return a light's features from its colour modes and effects."""
    modes = {str(mode) for mode in attributes.get("supported_color_modes") or ()}
    features: dict[str, Any] = {}
    if modes - {"onoff"}:
        features["brightness"] = True
    if "color_temp" in modes:
        low = attributes.get("min_color_temp_kelvin")
        high = attributes.get("max_color_temp_kelvin")
        if low and high:
            features["color_temp"] = {"min_k": int(low), "max_k": int(high)}
    if modes & COLOR_MODES:
        features["color"] = True
    effects = _clip_list(
        e
        for e in attributes.get("effect_list") or ()
        if str(e).lower() not in NO_EFFECTS
    )
    if effects:
        features["effects"] = effects
    return features


def climate_features(
    hass: HomeAssistant, attributes: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a climate entity's features."""
    supported = attributes.get(ATTR_SUPPORTED_FEATURES, 0)
    if supported & CLIMATE_RANGE:
        target = "range"
    elif supported & CLIMATE_TARGET:
        target = "single"
    else:
        target = "none"
    features: dict[str, Any] = {
        "target": target,
        "step": attributes.get("target_temp_step") or 0.5,
        "unit": hass.config.units.temperature_unit,
        "hvac_modes": _clip_list(attributes.get("hvac_modes") or ()),
        "presets": _clip_list(attributes.get("preset_modes") or ()),
        "fan_modes": _clip_list(attributes.get("fan_modes") or ()),
    }
    for name, attribute in (("min", "min_temp"), ("max", "max_temp")):
        if attributes.get(attribute) is not None:
            features[name] = attributes[attribute]
    return features


def cover_features(attributes: Mapping[str, Any]) -> dict[str, bool]:
    """Return a cover's features."""
    supported = attributes.get(ATTR_SUPPORTED_FEATURES, 0)
    return {
        "open": bool(supported & COVER_OPEN),
        "close": bool(supported & COVER_CLOSE),
        "stop": bool(supported & COVER_STOP),
        "position": bool(supported & COVER_POSITION),
        "tilt": bool(supported & COVER_TILT_POSITION),
    }


def fan_features(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fan's features; its speeds are `percentage_step` apart."""
    supported = attributes.get(ATTR_SUPPORTED_FEATURES, 0)
    features: dict[str, Any] = {"speed": bool(supported & FAN_SET_SPEED)}
    if features["speed"]:
        step = attributes.get("percentage_step")
        features["speed_step"] = round(float(step), 2) if step else 1
    presets = attributes.get("preset_modes") if supported & FAN_PRESET_MODE else None
    features["presets"] = _clip_list(presets or ())
    features["oscillate"] = bool(supported & FAN_OSCILLATE)
    features["direction"] = bool(supported & FAN_DIRECTION)
    return features


def builtin_icon(icon: object) -> str | None:
    """Return the built-in icon (appendix C) closest to a Material Design one."""
    if not isinstance(icon, str) or not icon.startswith("mdi:"):
        return None
    name = icon.removeprefix("mdi:")
    if name in ICONS:
        return name
    for alias, builtin in ICON_ALIASES.items():
        if name == alias or name.startswith(f"{alias}-"):
            return builtin
    for builtin in ICON_PREFIXES:
        if name.startswith(f"{builtin}-"):
            return builtin
    return None


def icon_for(state: State) -> str | None:
    """Return the closest built-in icon (appendix C), or None for the default."""
    if icon := builtin_icon(state.attributes.get(ATTR_ICON)):
        return icon
    device_class = state.attributes.get(ATTR_DEVICE_CLASS)
    return DEVICE_CLASS_ICONS.get((state.domain, device_class)) or DOMAIN_ICONS.get(
        state.domain
    )


def _short_name(name: str, area: ar.AreaEntry | None) -> str:
    """Drop the area's name from the front: the panel's title already says it."""
    if area is not None:
        prefix = f"{area.name} "
        if (
            name.casefold().startswith(prefix.casefold())
            and name[len(prefix) :].strip()
        ):
            rest = name[len(prefix) :].strip()
            return rest[0].upper() + rest[1:]
    return name


def _clip(text: str) -> str:
    return text if len(text) <= MAX_NAME else text[: MAX_NAME - 1] + "…"


def _clip_list(values: Iterable[Any]) -> list[str]:
    return [_clip(str(value)) for value in values][:MAX_OPTIONS]


# States ------------------------------------------------------------------------


def item_state(hass: HomeAssistant, item: Item) -> dict[str, Any]:
    """Return an item's state as section 10 sends it, `available` included."""
    if item.key == ALL_LIGHTS_KEY:
        return all_lights_state(hass, item)
    state = hass.states.get(item.entity_id)
    if state is None or state.state == STATE_UNAVAILABLE:
        return {"available": False}
    attributes = state.attributes
    fields: dict[str, Any]
    if item.kind == "light":
        fields = light_state(state)
    elif item.kind == "switch":
        fields = {"on": state.state == STATE_ON}
    elif item.kind == "climate":
        fields = {
            "hvac_mode": None if state.state == STATE_UNKNOWN else state.state,
            "hvac_action": attributes.get("hvac_action"),
            "current": attributes.get("current_temperature"),
            "target": attributes.get("temperature"),
            "target_low": attributes.get("target_temp_low"),
            "target_high": attributes.get("target_temp_high"),
            "preset": attributes.get("preset_mode"),
            "fan_mode": attributes.get("fan_mode"),
            "current_humidity": attributes.get("current_humidity"),
        }
    elif item.kind == "cover":
        position = attributes.get("current_position")
        tilt = attributes.get("current_tilt_position")
        fields = {
            "state": state.state
            if state.state in ("open", "closed", "opening", "closing")
            else "closed",
            "position": position,
            "tilt": tilt,
        }
    elif item.kind == "fan":
        direction = attributes.get("direction")
        fields = {
            "on": state.state == STATE_ON,
            "percentage": attributes.get("percentage"),
            "preset": attributes.get("preset_mode"),
            "oscillating": bool(attributes.get("oscillating")),
            "direction": direction if direction in FAN_DIRECTIONS else None,
        }
    elif item.kind == "lock":
        fields = {"state": state.state if state.state in LOCK_STATES else None}
    elif item.kind == "sensor":
        fields = {"value": sensor_value(hass, state)}
    else:
        fields = {}
    return {"available": True, **fields}


def light_state(state: State) -> dict[str, Any]:
    """Return a light's state; brightness as a percentage, colour as hs."""
    attributes = state.attributes
    on = state.state == STATE_ON
    brightness = attributes.get("brightness")
    hs = attributes.get("hs_color")
    effect = attributes.get("effect")
    color_mode = str(attributes.get("color_mode") or "")
    if color_mode == "color_temp":
        mode = "color_temp"
    elif color_mode in COLOR_MODES:
        mode = "color"
    else:
        mode = "brightness"
    return {
        "on": on,
        "brightness": round(brightness * 100 / 255) if on and brightness else 0,
        "color_temp_k": attributes.get("color_temp_kelvin"),
        "hs": [round(hs[0]), round(hs[1])] if hs else None,
        "effect": None
        if effect is None or str(effect).lower() in NO_EFFECTS
        else effect,
        "mode": mode,
    }


def all_lights_state(hass: HomeAssistant, item: Item) -> dict[str, Any]:
    """Return "all lights": on if any is, at the average brightness of those on."""
    states = [hass.states.get(entity_id) for entity_id in item.entity_ids]
    usable = [s for s in states if s is not None and s.state != STATE_UNAVAILABLE]
    if not usable:
        return {"available": False}
    lit = [light_state(s)["brightness"] for s in usable if s.state == STATE_ON]
    return {
        "available": True,
        "on": bool(lit),
        "brightness": round(sum(lit) / len(lit)) if lit else 0,
        "color_temp_k": None,
        "hs": None,
        "effect": None,
        "mode": "brightness",
    }


def sensor_value(hass: HomeAssistant, state: State) -> str:
    """Return a sensor's value with the precision Home Assistant shows."""
    if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return "—"
    entry = er.async_get(hass).async_get(state.entity_id)
    precision = None
    if entry is not None:
        sensor_options = entry.options.get("sensor", {})
        precision = sensor_options.get(
            "display_precision", sensor_options.get("suggested_display_precision")
        )
    if precision is not None:
        try:
            return f"{float(state.state):.{int(precision)}f}"
        except ValueError:
            pass
    return state.state


def diff(known: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """Return the fields of `new` the panel doesn't know yet."""
    return {name: value for name, value in new.items() if known.get(name, ...) != value}


# Intents -----------------------------------------------------------------------

LIGHT_ARGS: Final = {
    "brightness": ("brightness", "brightness_pct"),
    "color_temp_k": ("color_temp", "color_temp_kelvin"),
    "hs": ("color", "hs_color"),
    "effect": ("effects", "effect"),
}
# Action: the feature it needs, the service, and its arg here and there.
COVER_ACTIONS: Final = {
    "open": ("open", "open_cover", None, None),
    "close": ("close", "close_cover", None, None),
    "stop": ("stop", "stop_cover", None, None),
    "set_position": ("position", "set_cover_position", "position", "position"),
    "set_tilt": ("tilt", "set_cover_tilt_position", "tilt", "tilt_position"),
}
NUMBER = vol.All(vol.Coerce(float), vol.Range(min=-1000, max=1000))
PERCENT = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))
BOOLEAN = vol.Schema(bool)


async def async_handle_intent(
    hass: HomeAssistant, layout: Layout, request: Mapping[str, Any]
) -> None:
    """Carry out a panel's intent (section 11), or raise IntentError."""
    item = layout.item(str(request.get("key")))
    if item is None:
        raise IntentError("unknown_key", f"No item {request.get('key')}")
    action = request.get("action")
    args = request.get("args") or {}
    if not isinstance(action, str) or not isinstance(args, dict):
        raise IntentError("bad_request", "The intent needs an action and args")
    if not item_state(hass, item)["available"]:
        raise IntentError("unavailable", f"{item.name} is unavailable")
    domain, service, data = service_call(hass, item, action, args)
    data[ATTR_ENTITY_ID] = list(item.entity_ids)
    try:
        async with asyncio.timeout(INTENT_TIMEOUT):
            await hass.services.async_call(
                domain, service, data, blocking=True, context=Context()
            )
    except TimeoutError as err:
        raise IntentError("service_failed", f"{domain}.{service} timed out") from err
    except (HomeAssistantError, vol.Invalid, ValueError) as err:
        raise IntentError("service_failed", str(err) or type(err).__name__) from err


def service_call(
    hass: HomeAssistant, item: Item, action: str, args: Mapping[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    """Return the domain, service and data an intent becomes."""
    try:
        if item.kind == "light":
            return _light_call(hass, item, action, args)
        if item.kind == "switch" and action in ("toggle", "turn_on", "turn_off"):
            return item.domain, action, {}
        if item.kind == "scene" and action == "activate":
            return item.domain, "turn_on", {}
        if item.kind == "climate":
            return _climate_call(item, action, args)
        if item.kind == "fan":
            return _fan_call(item, action, args)
        if item.kind == "lock" and action in ("lock", "unlock"):
            return "lock", action, {}
        if item.kind == "lock" and action == "open" and item.features.get("open"):
            return "lock", "open", {}
        if item.kind == "cover" and action in COVER_ACTIONS:
            feature, service, arg, attribute = COVER_ACTIONS[action]
            if not item.features.get(feature):
                raise IntentError("unsupported", f"{item.name} can't {action}")
            data = {} if arg is None else {attribute: PERCENT(args.get(arg))}
            return "cover", service, data
    except vol.Invalid as err:
        raise IntentError("bad_request", f"Invalid args for {action}: {err}") from err
    raise IntentError("unsupported", f"{item.name} can't {action}")


def _light_call(
    hass: HomeAssistant, item: Item, action: str, args: Mapping[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    if action == "toggle" and item.key == ALL_LIGHTS_KEY:
        # light.toggle toggles each light on its own; the group goes one way.
        action = "turn_off" if all_lights_state(hass, item)["on"] else "turn_on"
    if action in ("toggle", "turn_off"):
        return "light", action, {}
    if action != "turn_on":
        raise IntentError("unsupported", f"{item.name} can't {action}")
    if len(args) > 1:
        raise IntentError("bad_request", "turn_on takes at most one of its args")
    data: dict[str, Any] = {}
    for name, value in args.items():
        if name not in LIGHT_ARGS:
            raise IntentError("bad_request", f"Unknown arg {name}")
        feature, attribute = LIGHT_ARGS[name]
        if not item.features.get(feature):
            raise IntentError("unsupported", f"{item.name} has no {feature}")
        if name == "brightness":
            data[attribute] = PERCENT(value)
        elif name == "color_temp_k":
            data[attribute] = vol.Coerce(int)(value)
        elif name == "hs":
            hue, saturation = vol.ExactSequence([NUMBER, NUMBER])(value)
            data[attribute] = [hue % 360, max(0.0, min(100.0, saturation))]
        else:
            if value not in item.features["effects"]:
                raise IntentError("unsupported", f"{item.name} has no effect {value}")
            data[attribute] = value
    return "light", "turn_on", data


def _fan_call(
    item: Item, action: str, args: Mapping[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    features = item.features
    if action in ("toggle", "turn_on", "turn_off"):
        return "fan", action, {}
    if action == "set_percentage" and features.get("speed"):
        return "fan", "set_percentage", {"percentage": PERCENT(args.get("percentage"))}
    if action == "set_preset":
        value = args.get("preset")
        if value not in features.get("presets", []):
            raise IntentError("unsupported", f"{item.name} has no preset {value}")
        return "fan", "set_preset_mode", {"preset_mode": value}
    if action == "oscillate" and features.get("oscillate"):
        return "fan", "oscillate", {"oscillating": BOOLEAN(args.get("oscillating"))}
    if action == "set_direction" and features.get("direction"):
        direction = vol.In(FAN_DIRECTIONS)(args.get("direction"))
        return "fan", "set_direction", {"direction": direction}
    raise IntentError("unsupported", f"{item.name} can't {action}")


def _climate_call(
    item: Item, action: str, args: Mapping[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    features = item.features
    if action == "set_target" and features.get("target") == "single":
        return "climate", "set_temperature", {"temperature": NUMBER(args.get("value"))}
    if action == "set_range" and features.get("target") == "range":
        return (
            "climate",
            "set_temperature",
            {
                "target_temp_low": NUMBER(args.get("low")),
                "target_temp_high": NUMBER(args.get("high")),
            },
        )
    for name, feature, service, arg, attribute in (
        ("set_hvac_mode", "hvac_modes", "set_hvac_mode", "mode", "hvac_mode"),
        ("set_preset", "presets", "set_preset_mode", "preset", "preset_mode"),
        ("set_fan_mode", "fan_modes", "set_fan_mode", "fan_mode", "fan_mode"),
    ):
        if action == name:
            value = args.get(arg)
            if value not in features.get(feature, []):
                raise IntentError("unsupported", f"{item.name} has no {arg} {value}")
            return "climate", service, {attribute: value}
    raise IntentError("unsupported", f"{item.name} can't {action}")
