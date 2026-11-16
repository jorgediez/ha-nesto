"""The panel's press gestures and notification answers, as events."""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    NestoConfigEntry,
    NestoCoordinator,
    signal_input,
    signal_notification,
)
from .entity import NestoEntity
from .notifications import EVENTS as NOTIFICATION_EVENTS

# Read only: events arrive pushed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the panel's events."""
    coordinator = entry.runtime_data
    async_add_entities(
        [NestoPressEvent(coordinator), NestoNotificationEvent(coordinator)]
    )


class NestoPressEvent(NestoEntity, EventEntity):
    """A press on the panel: single, double or long (PROTOCOL.md section 17).

    The event's `context` says where it happened: `ui` (it also acted on the
    controls), `standby` or `off`. A double press comes only in standby or
    off, and doesn't wake the panel.
    """

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_translation_key = "press"

    def __init__(self, coordinator: NestoCoordinator) -> None:
        """Initialise the event."""
        super().__init__(coordinator, "press")
        self._attr_event_types = ["single_press", "double_press", "long_press"]

    async def async_added_to_hass(self) -> None:
        """Listen for the panel's gestures."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_input(self.coordinator.config_entry.entry_id),
                self._async_press,
            )
        )

    @callback
    def _async_press(self, gesture: str, context: str) -> None:
        self._trigger_event(f"{gesture}_press", {"context": context})
        self.async_write_ha_state()


class NestoNotificationEvent(NestoEntity, EventEntity):
    """What happens to the panel's notifications (PROTOCOL.md section 14).

    The event's `id` is the notification's; an `action` event also has the
    `button`, the ID of the button pressed.
    """

    _attr_translation_key = "notification"

    def __init__(self, coordinator: NestoCoordinator) -> None:
        """Initialise the event."""
        super().__init__(coordinator, "notification")
        self._attr_event_types = list(NOTIFICATION_EVENTS)

    async def async_added_to_hass(self) -> None:
        """Listen for what happens to notifications."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_notification(self.coordinator.config_entry.entry_id),
                self._async_event,
            )
        )

    @callback
    def _async_event(self, event: str, nid: str, action: str | None) -> None:
        data = {"id": nid}
        if action is not None:
            data["button"] = action
        self._trigger_event(event, data)
        self.async_write_ha_state()
