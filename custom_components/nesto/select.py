"""The panel's choice settings: the standby screen and the clock's style."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NestoConfigEntry, NestoCoordinator
from .entity import NestoSettingEntity
from .settings import Path

# Settings are saved here and sent whole, so changes never get in each
# other's way.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NestoSelectDescription(SelectEntityDescription):
    """A choice setting, by its place in the `config` message."""

    path: Path


SELECTS: tuple[NestoSelectDescription, ...] = (
    # The carousel joins these once Home Assistant can send it images.
    NestoSelectDescription(
        key="standby_mode",
        path=("standby", "mode"),
        options=["clock", "off"],
    ),
    NestoSelectDescription(
        key="standby_clock_style",
        path=("standby", "clock", "style"),
        options=["analog", "digital"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the panel's choice settings."""
    async_add_entities(
        NestoSelect(entry.runtime_data, description) for description in SELECTS
    )


class NestoSelect(NestoSettingEntity, SelectEntity):
    """A choice setting."""

    entity_description: NestoSelectDescription

    def __init__(
        self, coordinator: NestoCoordinator, description: NestoSelectDescription
    ) -> None:
        """Initialise the select."""
        self.entity_description = description
        super().__init__(coordinator, description.path)

    @property
    def current_option(self) -> str:
        """Return the setting."""
        return self.setting

    async def async_select_option(self, option: str) -> None:
        """Change the setting."""
        await self.async_set(option)
