"""The panel's on/off settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NestoConfigEntry, NestoCoordinator
from .entity import NestoSettingEntity
from .settings import Path

# Settings are saved here and sent whole, so changes never get in each
# other's way.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NestoSwitchDescription(SwitchEntityDescription):
    """An on/off setting, by its place in the `config` message."""

    path: Path


def _switch(*path: str) -> NestoSwitchDescription:
    return NestoSwitchDescription(key="_".join(path), path=path)


SWITCHES: tuple[NestoSwitchDescription, ...] = (
    _switch("display", "auto_brightness"),
    _switch("display", "wake_on_motion"),
    _switch("display", "wake_on_input_when_off"),
    _switch("standby", "clock", "seconds"),
    _switch("standby", "clock", "date"),
    _switch("locale", "hour12"),
    _switch("sound", "ring_click"),
    _switch("sound", "press_click"),
    _switch("sound", "notifications"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the panel's on/off settings."""
    async_add_entities(
        NestoSwitch(entry.runtime_data, description) for description in SWITCHES
    )


class NestoSwitch(NestoSettingEntity, SwitchEntity):
    """An on/off setting."""

    entity_description: NestoSwitchDescription

    def __init__(
        self, coordinator: NestoCoordinator, description: NestoSwitchDescription
    ) -> None:
        """Initialise the switch."""
        self.entity_description = description
        super().__init__(coordinator, description.path)

    @property
    def is_on(self) -> bool:
        """Return the setting."""
        return self.setting

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""
        await self.async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""
        await self.async_set(False)
