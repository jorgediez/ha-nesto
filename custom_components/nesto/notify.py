"""The panel as a notify entity, for Home Assistant's `notify.send_message`."""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NestoConfigEntry, NestoCoordinator
from .entity import NestoEntity
from .notifications import build_notification

# Requests are matched to their results, so messages never get in each
# other's way.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the panel's notify entity."""
    async_add_entities([NestoNotify(entry.runtime_data)])


class NestoNotify(NestoEntity, NotifyEntity):
    """A normal notification with the panel's defaults.

    For priorities, buttons and the rest, there's the `nesto.notify` action.
    """

    _attr_translation_key = "notifications"

    def __init__(self, coordinator: NestoCoordinator) -> None:
        """Initialise the notify entity."""
        super().__init__(coordinator, "notify")

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Show the message on the panel."""
        await self.coordinator.async_notify(
            build_notification({"message": message, "title": title})
        )
