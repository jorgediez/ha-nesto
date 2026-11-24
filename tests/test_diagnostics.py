"""Diagnostics leave out the key, the address and the panel id."""

from __future__ import annotations

import json

import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.nesto.client import CannotConnect
from custom_components.nesto.coordinator import NotPaired, WrongPanel
from homeassistant.core import HomeAssistant

from .common import HOST, KEY, PANEL_ID, ClientFactory


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
    assert result["display"] is None
    assert result["status"] is None
    assert result["sensors"] == []
    assert result["settings"]["display"]["brightness"] == 80
    assert "t" not in result["settings"]
    assert result["room"] == {
        "mode": "room",
        "sections": {
            "lights": 0,
            "climate": 0,
            "covers": 0,
            "locks": 0,
            "switches": 0,
            "media_players": 0,
            "scenes": 0,
            "sensors": 0,
        },
        "all_lights": False,
        "saved_for_area": False,
        "removed": 0,
        "items": {},
        "waiting_for_state": 0,
        "left_out": 0,
    }


async def test_diagnostics_with_the_display(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
) -> None:
    clients.last.on_message(
        {"t": "display_state", "state": "standby", "brightness": 20, "cause": "sync"}
    )
    result = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)
    assert result["display"] == {"state": "standby", "brightness": 20}


async def test_diagnostics_with_status_and_sensors(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
) -> None:
    """The status is there, but not the panel's address."""

    async def request(message, _seconds=None):
        return {
            "t": "result",
            "ok": True,
            "uptime_s": 60,
            "free_ram_kb": 1000,
            "ip": "192.168.1.61",
            "base_connected": True,
            "base_fw": "1.2.3",
        }

    clients.last.request.side_effect = request
    clients.last.on_message({"t": "sensors", "temperature_c": 21.0})
    clients.last.on_message({"t": "ready"})
    await hass.async_block_till_done(wait_background_tasks=True)
    result = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)
    assert "192.168.1.61" not in json.dumps(result)
    assert result["status"]["free_ram_kb"] == 1000
    assert result["status"]["base_connected"] is True
    assert "ip" not in result["status"]
    assert result["sensors"] == ["temperature_c"]
    assert result["temperature_offset"] == 0.0


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        # How the client words a failed connection, with the socket's error.
        (
            CannotConnect(
                f"{HOST}:6170: [Errno 111] Connect call failed ('{HOST}', 6170)"
            ),
            "CannotConnect: **REDACTED**:6170: [Errno 111] Connect call failed "
            "('**REDACTED**', 6170)",
        ),
        # A panel added by name: the socket's error names its IP address.
        (
            CannotConnect("[Errno 113] Connect call failed ('192.168.1.61', 6170)"),
            "CannotConnect: [Errno 113] Connect call failed ('**REDACTED**', 6170)",
        ),
        (
            CannotConnect("Connect call failed ('fe80::1%eth0', 6170, 0, 2)"),
            "CannotConnect: Connect call failed ('**REDACTED**%eth0', 6170, 0, 2)",
        ),
        # Another panel at the saved address.
        (
            WrongPanel(f"{HOST} is panel 0A1B2C3D4E5F now"),
            "WrongPanel: **REDACTED** is panel **REDACTED** now",
        ),
        (
            NotPaired(f"panel {PANEL_ID} holds no pairing key"),
            "NotPaired: panel **REDACTED** holds no pairing key",
        ),
        # Nothing to hide: as it was.
        (
            CannotConnect("timed out after 10 s at 12:30"),
            "CannotConnect: timed out after 10 s at 12:30",
        ),
    ],
)
async def test_the_last_error_hides_addresses_and_panel_ids(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    error: Exception,
    shown: str,
) -> None:
    init_integration.runtime_data.last_error = error
    result = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)
    assert result["connection"]["last_error"] == shown
