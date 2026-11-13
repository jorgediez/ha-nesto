"""The Nesto Panel integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)

from .client import AuthenticationFailed, NestoClient, NestoError, UnsupportedProtocol
from .const import CONF_KEY, DOMAIN, UNPAIR_TIMEOUT
from .coordinator import NestoConfigEntry, NestoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: NestoConfigEntry) -> bool:
    """Connect to the panel, then set up its entities."""
    coordinator = NestoCoordinator(hass, entry)
    placeholders = {"name": entry.title, "host": entry.data[CONF_HOST]}
    try:
        await coordinator.async_connect()
    except AuthenticationFailed as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="auth_failed",
            translation_placeholders=placeholders,
        ) from err
    except UnsupportedProtocol as err:
        coordinator.async_report_unsupported(err)
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="unsupported_protocol",
            translation_placeholders={**placeholders, "version": str(err.version)},
        ) from err
    except NestoError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={**placeholders, "error": str(err)},
        ) from err

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_shutdown()
        raise

    entry.runtime_data = coordinator
    coordinator.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NestoConfigEntry) -> bool:
    """Unload the entities, then close the connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: NestoConfigEntry) -> None:
    """Tell the panel to forget the key, so it waits to be paired again.

    Best effort: a panel that's off or gone is removed all the same, and keeps
    a key nobody holds until someone pairs it or chooses Forget on it.
    """
    client = NestoClient(entry.data[CONF_HOST], entry.data[CONF_PORT])
    try:
        async with asyncio.timeout(UNPAIR_TIMEOUT):
            hello = await client.connect()
            if hello.panel_id != entry.unique_id or not hello.paired:
                return
            await client.authenticate(bytes.fromhex(entry.data[CONF_KEY]))
            await client.request({"t": "unpair"})
            _LOGGER.debug("Panel %s forgot its pairing key", entry.title)
    except (NestoError, TimeoutError) as err:
        _LOGGER.debug("Couldn't unpair panel %s: %s", entry.title, err)
    finally:
        with contextlib.suppress(NestoError):
            await client.close()
