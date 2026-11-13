"""The panel's Identify button."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NestoConfigEntry, NestoCoordinator
from .entity import NestoEntity

# Requests are matched to their results, so presses never get in each other's
# way.
PARALLEL_UPDATES = 0

# Identifying takes about 3 s on the panel; allow for a slow network on top.
IDENTIFY_TIMEOUT = 10.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the panel's buttons."""
    async_add_entities([NestoIdentifyButton(entry.runtime_data)])


class NestoIdentifyButton(NestoEntity, ButtonEntity):
    """Wake the panel, pulse its backlight and beep, to find it."""

    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NestoCoordinator) -> None:
        """Initialise the button."""
        super().__init__(coordinator, "identify")

    async def async_press(self) -> None:
        """Ask the panel to identify itself (PROTOCOL.md section 18)."""
        await self.coordinator.async_request({"t": "identify"}, IDENTIFY_TIMEOUT)
