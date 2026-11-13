"""Shared fixtures: a mocked panel client and a loaded config entry.

pytest-homeassistant enables custom integrations by default, and the minimum
Home Assistant is 2026.11, so no auto_enable_custom_integrations fixture is
needed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from unittest.mock import patch

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.nesto.const import CONF_KEY, CONF_PANEL_ID, DOMAIN
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from .common import HOST, KEY, PANEL_ID, PORT, TITLE, ClientFactory


@pytest.fixture
def clients() -> Generator[ClientFactory]:
    """Patch NestoClient everywhere the integration makes one."""
    factory = ClientFactory()
    with (
        patch("custom_components.nesto.NestoClient", factory),
        patch("custom_components.nesto.coordinator.NestoClient", factory),
        patch("custom_components.nesto.config_flow.NestoClient", factory),
    ):
        yield factory


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a panel's config entry, shaped as the config flow creates it."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=TITLE,
        unique_id=PANEL_ID,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_KEY: KEY.hex(),
            CONF_PANEL_ID: PANEL_ID,
        },
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, clients: ClientFactory
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration, and unload it afterwards to stop its timers."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
