"""The panel's sensors, and how it stands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NestoConfigEntry, NestoCoordinator
from .entity import NestoEntity

# Read only: everything arrives pushed, or with the status the coordinator
# asks for.
PARALLEL_UPDATES = 0

TEMPERATURE_OFFSET = ("local", "temperature_offset")


def _reading(name: str) -> Callable[[NestoCoordinator], Any]:
    return lambda coordinator: coordinator.data.sensors.get(name)


def _temperature(coordinator: NestoCoordinator) -> float | None:
    raw = coordinator.data.sensors.get("temperature_c")
    if raw is None:
        return None
    return round(raw + coordinator.settings.get(TEMPERATURE_OFFSET), 2)


def _status(name: str) -> Callable[[NestoCoordinator], Any]:
    def value(coordinator: NestoCoordinator) -> Any:
        status = coordinator.data.status
        return None if status is None else getattr(status, name)

    return value


@dataclass(frozen=True, kw_only=True)
class NestoSensorDescription(SensorEntityDescription):
    """A sensor, and the panel capability it needs (PROTOCOL.md section 5)."""

    value_fn: Callable[[NestoCoordinator], Any]
    # The name in the hello's `caps.sensors`; None for the panel's status,
    # which every panel reports.
    cap: str | None = None


def _voltage(key: str, cap: str, field: str, **kwargs: Any) -> NestoSensorDescription:
    return NestoSensorDescription(
        key=key,
        cap=cap,
        value_fn=_reading(field),
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        **kwargs,
    )


SENSORS: tuple[NestoSensorDescription, ...] = (
    NestoSensorDescription(
        key="temperature",
        cap="temperature",
        value_fn=_temperature,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
    ),
    NestoSensorDescription(
        key="humidity",
        cap="humidity",
        value_fn=_reading("humidity_pct"),
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
    ),
    # The light sensor's raw value, higher brighter: lux would need a lux
    # meter to map it, so it has no illuminance device class.
    NestoSensorDescription(
        key="illuminance",
        cap="illuminance",
        value_fn=_reading("illuminance_raw"),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _voltage("battery_voltage", "battery_voltage", "battery_v"),
    _voltage(
        "supply_voltage",
        "supply_voltage",
        "supply_v",
        entity_registry_enabled_default=False,
    ),
    _voltage(
        "usb_voltage", "usb_voltage", "usb_v", entity_registry_enabled_default=False
    ),
    NestoSensorDescription(
        key="last_restart",
        value_fn=_status("last_restart"),
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    NestoSensorDescription(
        key="free_memory",
        value_fn=_status("free_ram_kb"),
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfInformation.KIBIBYTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NestoSensorDescription(
        key="wifi_signal",
        value_fn=_status("wifi_rssi"),
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NestoSensorDescription(
        key="ip_address",
        value_fn=_status("ip"),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NestoSensorDescription(
        key="base_firmware",
        value_fn=_status("base_fw"),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors the panel has, and its status."""
    coordinator = entry.runtime_data
    caps = coordinator.sensor_caps or frozenset()
    async_add_entities(
        NestoSensor(coordinator, description)
        for description in SENSORS
        if description.cap is None or description.cap in caps
    )


class NestoSensor(NestoEntity, SensorEntity):
    """A sensor of the panel."""

    entity_description: NestoSensorDescription

    def __init__(
        self, coordinator: NestoCoordinator, description: NestoSensorDescription
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_translation_key = description.key

    @property
    def native_value(self) -> float | int | str | datetime | None:
        """Return the last reading, or None while the panel hasn't sent one."""
        return self.entity_description.value_fn(self.coordinator)
