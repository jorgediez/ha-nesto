"""The Identify and Restart buttons."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.nesto import coordinator as coordinator_module
from custom_components.nesto.client import CannotConnect, PanelError
from custom_components.nesto.coordinator import PanelData
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .common import ClientFactory, make_hello

REBOOT_CAPS = {"beeper": True, "input": ["ring", "press"], "reboot": True}


def button_entity(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        BUTTON_DOMAIN, "nesto", f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None
    return entity_id


def identify_entity(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    return button_entity(hass, entry, "identify")


def restart_entity(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    return button_entity(hass, entry, "restart")


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


@pytest.fixture
def can_reboot(clients: ClientFactory) -> ClientFactory:
    """A panel running Nesto 0.2.1 or later."""
    clients.hello = make_hello(fw="0.2.1", caps=REBOOT_CAPS)
    return clients


async def test_unique_id_and_category(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The unique id format is fixed: changing it orphans users' entities."""
    entity = er.async_get(hass).async_get(identify_entity(hass, init_integration))
    assert entity.unique_id == f"{init_integration.entry_id}_identify"
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.original_device_class == "identify"


async def test_press(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: identify_entity(hass, init_integration)},
        blocking=True,
    )
    clients.last.request.assert_awaited_once()
    assert clients.last.request.await_args.args[0] == {"t": "identify"}


async def test_press_fails(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.request_error = CannotConnect("no answer")
    with pytest.raises(HomeAssistantError) as caught:
        await hass.services.async_call(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: identify_entity(hass, init_integration)},
            blocking=True,
        )
    assert caught.value.translation_key == "cannot_send"


async def test_unavailable_while_disconnected(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    entity_id = identify_entity(hass, init_integration)
    init_integration.runtime_data.async_set_updated_data(PanelData())
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


# Restart ---------------------------------------------------------------------


async def test_restart_unique_id_and_category(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The unique id format is fixed: changing it orphans users' entities."""
    entity = er.async_get(hass).async_get(restart_entity(hass, init_integration))
    assert entity.unique_id == f"{init_integration.entry_id}_restart"
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.original_device_class == "restart"


async def test_restart_unavailable_without_reboot(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    """Nesto 0.2.0 and older can't reboot: the button is there, unavailable."""
    entity_id = restart_entity(hass, init_integration)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    await press(hass, entity_id)  # Home Assistant skips an unavailable entity
    assert {"t": "reboot"} not in clients.last.sent


@pytest.mark.usefixtures("can_reboot")
async def test_restart(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    entity_id = restart_entity(hass, init_integration)
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE
    await press(hass, entity_id)
    assert clients.last.request.await_args.args[0] == {"t": "reboot"}


@pytest.mark.usefixtures("can_reboot")
async def test_restart_refused(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    """During a firmware update, the panel refuses."""
    clients.request_error = PanelError("bad_request", "an update is in progress")
    with pytest.raises(HomeAssistantError) as caught:
        await press(hass, restart_entity(hass, init_integration))
    assert caught.value.translation_key == "cannot_send"
    assert "an update is in progress" in str(caught.value)


async def test_restart_after_an_update(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    """A panel that comes back able to reboot gets the button, without a reload."""
    entity_id = restart_entity(hass, init_integration)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    clients.hello = make_hello(fw="0.2.1", caps=REBOOT_CAPS)
    with patch.object(coordinator_module, "RECONNECT_DELAYS", (0,)):
        clients.drop()
        await hass.async_block_till_done()
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE
