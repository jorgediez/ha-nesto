"""The panel's display, as a light."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.color import brightness_to_value, value_to_brightness

from .coordinator import NestoConfigEntry, NestoCoordinator
from .entity import NestoEntity

# Requests are matched to their results, and settings are sent whole, so
# commands never get in each other's way.
PARALLEL_UPDATES = 0

# The panel's brightness scale, in percent.
PERCENT = (1, 100)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the panel's display."""
    async_add_entities([NestoDisplayLight(entry.runtime_data)])


class NestoDisplayLight(NestoEntity, LightEntity):
    """The display: on while active or in standby, off when the screen is off.

    Its brightness is what the screen shows now, which depends on the state,
    and on the room's light with auto brightness (PROTOCOL.md sections 12, 13).
    """

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_translation_key = "display"

    def __init__(self, coordinator: NestoCoordinator) -> None:
        """Initialise the light."""
        super().__init__(coordinator, "display")
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def is_on(self) -> bool | None:
        """Return whether the screen is lit, or None before the panel says."""
        display = self.coordinator.data.display
        return None if display is None else display.state != "off"

    @property
    def brightness(self) -> int | None:
        """Return the screen's brightness now."""
        display = self.coordinator.data.display
        if display is None:
            return None
        if display.brightness <= 0:
            return 0
        return value_to_brightness(PERCENT, display.brightness)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Wake the display; a brightness also becomes its Brightness setting."""
        if ATTR_BRIGHTNESS in kwargs:
            percent = brightness_to_value(PERCENT, kwargs[ATTR_BRIGHTNESS])
            await self.coordinator.async_set_setting(
                ("display", "brightness"), max(1, round(percent))
            )
        await self.coordinator.async_set_display("active")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the screen off."""
        await self.coordinator.async_set_display("off")
