"""Diagnostics for Nesto Panel, for bug reports.

They say how the panel and its connection stand: the firmware, model,
protocol and capabilities the panel announced, whether it's connected, and
the last error. Left out: the pairing key, which works like a password, and
the panel's address and id (its Wi-Fi MAC address), which identify the
device and the network.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_KEY, CONF_PANEL_ID
from .coordinator import NestoConfigEntry

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
    }
