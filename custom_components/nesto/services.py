"""The panel's actions: show a notification, and clear them."""

from __future__ import annotations

from typing import Any, Final

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID, ATTR_ICON
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import NestoConfigEntry
from .notifications import BEEPS, MAX_DURATION, PRIORITIES, build_notification

SERVICE_NOTIFY: Final = "notify"
SERVICE_CLEAR: Final = "clear_notification"

ATTR_MESSAGE: Final = "message"
ATTR_TITLE: Final = "title"
ATTR_ID: Final = "id"
ATTR_PRIORITY: Final = "priority"
ATTR_DURATION: Final = "duration"
ATTR_BEEP: Final = "beep"
ATTR_BUTTONS: Final = "buttons"

NOTIFY_SCHEMA: Final = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_TITLE): cv.string,
        vol.Optional(ATTR_ID): cv.string,
        vol.Optional(ATTR_PRIORITY): vol.In(PRIORITIES),
        vol.Optional(ATTR_DURATION): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_DURATION)
        ),
        vol.Optional(ATTR_BEEP): vol.In(BEEPS),
        vol.Optional(ATTR_ICON): cv.icon,
        vol.Optional(ATTR_BUTTONS): vol.All(cv.ensure_list, [cv.string]),
    }
)
CLEAR_SCHEMA: Final = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_ID): cv.string,
    }
)


def _entry(hass: HomeAssistant, entry_id: str) -> NestoConfigEntry:
    """Return the panel's loaded entry, or raise."""
    entry: NestoConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_panel",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="panel_not_loaded",
            translation_placeholders={"name": entry.title},
        )
    return entry


async def _async_notify(call: ServiceCall) -> ServiceResponse:
    entry = _entry(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    request = build_notification(call.data)
    await entry.runtime_data.async_notify(request)
    response: dict[str, Any] = {ATTR_ID: request["nid"]}
    return response


async def _async_clear(call: ServiceCall) -> None:
    entry = _entry(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    await entry.runtime_data.async_clear_notifications(call.data.get(ATTR_ID))


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions, once for every panel."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_NOTIFY,
        _async_notify,
        schema=NOTIFY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR, _async_clear, schema=CLEAR_SCHEMA
    )
