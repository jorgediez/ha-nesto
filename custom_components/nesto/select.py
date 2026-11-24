"""The panel's choice settings.

The standby screen, the clock's style, and the item the panel wakes on.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Final

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NestoConfigEntry, NestoCoordinator
from .entity import NestoSettingEntity
from .room import target_of
from .settings import WAKE, Path

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
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(NestoSelect(coordinator, description) for description in SELECTS),
            NestoWakeSelect(coordinator),
        ]
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


# The Wake on option for the top of the list.
FIRST: Final = "first"


class NestoWakeSelect(NestoSettingEntity, SelectEntity):
    """The item the panel wakes on: the top of the list, or one of its items.

    Kept by entity, so a renamed item stays chosen; while the entity isn't on
    the panel, the panel wakes at the top. Available only on a panel that can
    use it (Nesto 0.3.0 and later report caps.show).
    """

    def __init__(self, coordinator: NestoCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator, WAKE)

    @property
    def available(self) -> bool:
        """Return whether the panel wakes on a chosen item."""
        hello = self.coordinator.hello
        return hello is not None and hello.caps.get("show") is True

    def _choices(self) -> dict[str, str]:
        """Return each option, and the entity it stands for."""
        layout = self.coordinator.layout
        items = layout.items if layout else ()
        names = Counter(item.name for item in items)
        choices = {FIRST: ""}
        for item in items:
            target = target_of(item)
            name = item.name
            if names[name] > 1 or name == FIRST:
                name = f"{name} ({target})"
            choices[name] = target
        return choices

    @property
    def options(self) -> list[str]:
        """Return the top of the list, then the panel's items."""
        return list(self._choices())

    @property
    def current_option(self) -> str:
        """Return the chosen item, or the top if it isn't on the panel."""
        wake = self.setting
        return next(
            (option for option, target in self._choices().items() if target == wake),
            FIRST,
        )

    async def async_select_option(self, option: str) -> None:
        """Choose the item."""
        await self.async_set(self._choices()[option])
