"""Diagnostics for Nesto Panel, for bug reports.

They say how the panel and its connection stand: the firmware, model,
protocol and capabilities the panel announced, whether it's connected, the
last error, how many items of each kind it shows, the display's state, the
panel's settings and status, and which sensors last reported. Left out: the
pairing key, which works like a password; the panel's address and id (its
Wi-Fi MAC address), which identify the device and the network; and which
entities and areas the panel shows, which describe the home.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ALL_LIGHTS,
    CONF_AREA,
    CONF_KEY,
    CONF_MODE,
    CONF_PANEL_ID,
    CONF_REMOVED,
    MODE_ROOM,
)
from .coordinator import NestoConfigEntry
from .room import SECTIONS

TO_REDACT = {CONF_HOST, CONF_KEY, CONF_PANEL_ID}

# What the panel's hello may report as is; anything else is left out.
HELLO_FIELDS = ("proto", "fw", "model", "paired", "pairing_open", "caps")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NestoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    hello = coordinator.hello
    error = coordinator.last_error
    layout = coordinator.layout
    options = entry.options
    display = coordinator.data.display
    status = coordinator.data.status
    settings = coordinator.settings.as_config()
    del settings["t"]
    return {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "panel": (
            {field: getattr(hello, field) for field in HELLO_FIELDS}
            if hello is not None
            else None
        ),
        "connection": {
            "connected": coordinator.data.connected,
            "ready": coordinator.data.ready,
            "last_error": f"{type(error).__name__}: {error}" if error else None,
        },
        "display": asdict(display) if display is not None else None,
        "settings": settings,
        "temperature_offset": coordinator.settings.get(("local", "temperature_offset")),
        # The panel's address is left out, like the entry's.
        "status": (
            {k: v for k, v in asdict(status).items() if k != "ip"}
            if status is not None
            else None
        ),
        "sensors": sorted(coordinator.data.sensors),
        "room": {
            "mode": options.get(CONF_MODE, MODE_ROOM),
            # How many entities each section lists, not which.
            "sections": {name: len(options.get(name, [])) for name in SECTIONS},
            "all_lights": options.get(CONF_ALL_LIGHTS, False),
            "saved_for_area": CONF_AREA in options,
            "removed": len(options.get(CONF_REMOVED, [])),
            "items": (
                dict(Counter(item.kind for item in layout.items))
                if layout is not None
                else None
            ),
            "waiting_for_state": len(layout.waiting) if layout is not None else None,
            "left_out": len(layout.left_out) if layout is not None else None,
        },
    }
