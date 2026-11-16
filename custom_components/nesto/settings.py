"""The panel's settings, which Home Assistant keeps (PROTOCOL.md section 12).

The panel keeps no settings of its own: Home Assistant sends the whole `config`
object every time, and the panel stores the latest copy only to show its clock
before Home Assistant connects. So the settings live here, one store per panel,
starting from the firmware's own defaults: until the user changes one, the
panel behaves as it would with no settings at all.

A few settings are Home Assistant's own, such as the temperature offset: they
are kept here too, under `local`, and never sent.
"""

from __future__ import annotations

import copy
from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION: Final = 1
# Seconds to gather changes before writing the store, as a slider sends many.
SAVE_DELAY: Final = 1

type Path = tuple[str, ...]

# The firmware's defaults (src/room/DisplayPower.hpp), which are the protocol's
# example. The carousel and the language come later: the firmware has neither
# yet, so they're left out and the panel keeps its own.
DEFAULTS: Final[dict[str, Any]] = {
    "display": {
        "brightness": 80,
        "auto_brightness": True,
        "timeout_s": 30,
        "wake_on_motion": True,
        "wake_on_input_when_off": True,
    },
    "standby": {
        "mode": "clock",
        "brightness": 20,
        "clock": {"style": "analog", "seconds": False, "date": True},
    },
    "locale": {"hour12": False},
    "sound": {"ring_click": True, "press_click": False, "notifications": True},
}

# Home Assistant's own settings for the panel, never sent to it.
LOCAL: Final = "local"
LOCAL_DEFAULTS: Final[dict[str, Any]] = {
    # Added to the raw temperature, which the head unit's own warmth raises.
    "temperature_offset": 0.0,
}
ALL_DEFAULTS: Final[dict[str, Any]] = {**DEFAULTS, LOCAL: LOCAL_DEFAULTS}


def _merge(defaults: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    """Return the defaults with what was stored on top.

    Only known settings of the right type are taken, so a store written by a
    later version, or by hand, can't send the panel something it won't take.
    """
    merged: dict[str, Any] = {}
    for key, default in defaults.items():
        value = stored.get(key)
        if isinstance(default, dict):
            merged[key] = _merge(default, value if isinstance(value, dict) else {})
        elif type(value) is type(default):
            merged[key] = value
        elif type(default) is float and type(value) is int:
            merged[key] = float(value)
        else:
            merged[key] = default
    return merged


class Settings:
    """One panel's settings, kept in Home Assistant's storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialise with the defaults; `async_load` reads what was stored."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self._data = copy.deepcopy(ALL_DEFAULTS)

    async def async_load(self) -> None:
        """Read the stored settings, if any."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._data = _merge(ALL_DEFAULTS, stored)

    def get(self, path: Path) -> Any:
        """Return one setting."""
        value: Any = self._data
        for key in path:
            value = value[key]
        return value

    def set(self, path: Path, value: Any) -> bool:
        """Change one setting and save it soon; return whether it changed."""
        *sections, key = path
        section = self._data
        for name in sections:
            section = section[name]
        if section[key] == value:
            return False
        section[key] = value
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY)
        return True

    def as_config(self) -> dict[str, Any]:
        """Return the `config` message to send, without the local settings."""
        config = copy.deepcopy(self._data)
        del config[LOCAL]
        return {"t": "config", **config}

    async def async_remove(self) -> None:
        """Delete the stored settings, when the panel is removed."""
        await self._store.async_remove()
