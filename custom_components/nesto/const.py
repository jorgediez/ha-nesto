"""Constants for the Nesto Panel integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "nesto"

CONF_KEY: Final = "key"  # the pairing key, as hex
CONF_PANEL_ID: Final = "panel_id"

# Options: what the panel shows.
CONF_MODE: Final = "mode"
MODE_ROOM: Final = "room"  # the area the panel's device is in
MODE_MANUAL: Final = "manual"  # the entities the user picks
CONF_EXCLUDE: Final = "exclude"  # room mode: entities to leave out
CONF_SENSORS: Final = "sensors"  # room mode: sensors to add
CONF_ENTITIES: Final = "entities"  # manual mode

DEFAULT_PORT: Final = 6170
DEFAULT_NAME: Final = "Nesto Panel"

MANUFACTURER: Final = "Nesto"
# The hardware each firmware model runs on, for the device's model name.
MODELS: Final = {"nest-gen2": "Nest Learning Thermostat (2nd generation)"}

# Seconds between attempts to reach a panel again, the last one repeating.
RECONNECT_DELAYS: Final = (1, 2, 5, 10, 30, 60)
# How long removing an entry waits to tell the panel to forget the key.
UNPAIR_TIMEOUT: Final = 5.0
# How long a panel's intent waits for its service call (PROTOCOL.md section 11).
INTENT_TIMEOUT: Final = 10.0


def panel_title(panel_id: str) -> str:
    """Return a panel's name as its mDNS announcement gives it.

    The last six hex digits of its Wi-Fi MAC address (PROTOCOL.md section 2).
    """
    return f"{DEFAULT_NAME} {panel_id[-6:]}"
