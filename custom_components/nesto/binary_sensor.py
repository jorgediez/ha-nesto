"""The panel's motion sensors, and whether its base answers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NestoConfigEntry, NestoCoordinator
from .entity import NestoEntity

# Read only: everything arrives pushed, or with the status the coordinator
# asks for.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NestoBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor, and the panel capability it needs."""

    value_fn: Callable[[NestoCoordinator], bool | None]
    cap: str | None = None  # as in sensor.py


def _base_connected(coordinator: NestoCoordinator) -> bool | None:
    status = coordinator.data.status
    return None if status is None else status.base_connected


BINARY_SENSORS: tuple[NestoBinarySensorDescription, ...] = (
    # The room's motion sensor: on at once, off 30 s after the last movement.
    NestoBinarySensorDescription(
        key="motion",
        cap="motion_far",
        value_fn=lambda c: c.data.sensors.get("motion_far"),
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    # The proximity sensor: a hand near the glass, off 5 s after it's gone.
    NestoBinarySensorDescription(
        key="proximity",
        cap="motion_near",
        value_fn=lambda c: c.data.sensors.get("motion_near"),
        device_class=BinarySensorDeviceClass.PRESENCE,
    ),
    NestoBinarySensorDescription(
        key="base_connected",
        value_fn=_base_connected,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors the panel has."""
    coordinator = entry.runtime_data
    caps = coordinator.sensor_caps or frozenset()
    async_add_entities(
        NestoBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if description.cap is None or description.cap in caps
    )


class NestoBinarySensor(NestoEntity, BinarySensorEntity):
    """A binary sensor of the panel."""

    entity_description: NestoBinarySensorDescription

    def __init__(
        self, coordinator: NestoCoordinator, description: NestoBinarySensorDescription
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_translation_key = description.key

    @property
    def is_on(self) -> bool | None:
        """Return the last reading, or None while the panel hasn't sent one."""
        return self.entity_description.value_fn(self.coordinator)
