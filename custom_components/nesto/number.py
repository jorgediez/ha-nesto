"""The panel's number settings: brightness and the screen timeout."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NestoConfigEntry, NestoCoordinator
from .entity import NestoSettingEntity
from .settings import Path

# Settings are saved here and sent whole, so changes never get in each
# other's way.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NestoNumberDescription(NumberEntityDescription):
    """A number setting, by its place in the `config` message."""

    path: Path


NUMBERS: tuple[NestoNumberDescription, ...] = (
    # Not 0: an active screen at 0 would look off, and off is its own state.
    NestoNumberDescription(
        key="display_brightness",
        path=("display", "brightness"),
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
    ),
    NestoNumberDescription(
        key="standby_brightness",
        path=("standby", "brightness"),
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
    ),
    # 0 means never; the firmware takes up to a day.
    NestoNumberDescription(
        key="display_timeout_s",
        path=("display", "timeout_s"),
        native_min_value=0,
        native_max_value=86400,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=NumberDeviceClass.DURATION,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the panel's number settings."""
    async_add_entities(
        NestoNumber(entry.runtime_data, description) for description in NUMBERS
    )


class NestoNumber(NestoSettingEntity, NumberEntity):
    """A number setting."""

    entity_description: NestoNumberDescription

    def __init__(
        self, coordinator: NestoCoordinator, description: NestoNumberDescription
    ) -> None:
        """Initialise the number."""
        self.entity_description = description
        super().__init__(coordinator, description.path)

    @property
    def native_value(self) -> int:
        """Return the setting."""
        return self.setting

    async def async_set_native_value(self, value: float) -> None:
        """Change the setting; the panel takes whole numbers."""
        await self.async_set(round(value))
