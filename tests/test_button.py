"""The Identify button."""

from __future__ import annotations

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.nesto.client import CannotConnect
from custom_components.nesto.coordinator import PanelData
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .common import ClientFactory


def identify_entity(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        BUTTON_DOMAIN, "nesto", f"{entry.entry_id}_identify"
    )
    assert entity_id is not None
    return entity_id


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
