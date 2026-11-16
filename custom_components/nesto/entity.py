"""Base entity for Nesto Panel."""

from __future__ import annotations

from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODELS
from .coordinator import NestoCoordinator
from .settings import Path


class NestoEntity(CoordinatorEntity[NestoCoordinator]):
    """An entity of the one device each panel is.

    Every entity describes the device in full. Platforms can set up in any
    order, and an entity that only named the device by its identifier could
    register first, before anything had given the device a name.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: NestoCoordinator, key: str) -> None:
        """Initialise from the shared coordinator; `key` tells entities apart."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"
        # Entities are set up once the panel has connected, so it has said
        # what it is.
        hello = coordinator.hello
        model = hello.model if hello else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.panel_id)},
            # The panel id is the Wi-Fi MAC address (PROTOCOL.md section 2).
            connections={(CONNECTION_NETWORK_MAC, format_mac(coordinator.panel_id))},
            manufacturer=MANUFACTURER,
            model=MODELS.get(model, model) if model else None,
            model_id=model,
            name=coordinator.config_entry.title,
            sw_version=hello.fw if hello else None,
        )

    @property
    def available(self) -> bool:
        """Return whether the panel is connected."""
        return super().available and self.coordinator.data.connected


class NestoSettingEntity(NestoEntity):
    """One of the panel's settings, which Home Assistant keeps.

    It stays available while the panel is away: a change is saved, and the
    panel gets it when it connects again.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NestoCoordinator, path: Path) -> None:
        """Initialise; the setting's path is also its key and translation key."""
        key = "_".join(path)
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._path = path

    @property
    def available(self) -> bool:
        """Return True: the setting is Home Assistant's, panel or not."""
        return True

    @property
    def setting(self) -> Any:
        """Return the setting's value."""
        return self.coordinator.settings.get(self._path)

    async def async_set(self, value: Any) -> None:
        """Change the setting."""
        await self.coordinator.async_set_setting(self._path, value)
