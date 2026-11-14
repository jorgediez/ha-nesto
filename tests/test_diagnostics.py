"""Diagnostics leave out the key, the address and the panel id."""

from __future__ import annotations

import json

from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.nesto.client import CannotConnect
from homeassistant.core import HomeAssistant

from .common import HOST, KEY, PANEL_ID


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    init_integration.runtime_data.last_error = CannotConnect("refused")
    result = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)

    text = json.dumps(result)
    for secret in (KEY.hex(), HOST, PANEL_ID):
        assert secret not in text
    assert result["entry"]["data"]["key"] == "**REDACTED**"
    assert result["panel"] == {
        "proto": 1,
        "fw": "0.1.0",
        "model": "nest-gen2",
        "paired": True,
        "pairing_open": False,
        "caps": {"beeper": True, "input": ["ring", "press"]},
    }
    assert result["connection"] == {
        "connected": True,
        "ready": False,
        "last_error": "CannotConnect: refused",
    }
