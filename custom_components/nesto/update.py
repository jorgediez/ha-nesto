"""The panel's firmware: what runs, the latest release, and installing it.

Installing downloads the release's program and signature, sends them to the
panel in pieces (PROTOCOL.md section 19), and waits for the panel to come
back with the new version. The panel checks the signature, so Home
Assistant only carries the bytes; a panel that can't start the new program,
or doesn't reach Home Assistant with it, goes back to the old one.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import aiohttp

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import NestoError, PanelError, b64
from .const import (
    DOMAIN,
    FIRMWARE_CHUNK,
    FIRMWARE_DOWNLOAD_TIMEOUT,
    FIRMWARE_MAX_SIZE,
    FIRMWARE_RESTART_TIMEOUT,
)
from .coordinator import NestoConfigEntry, NestoCoordinator
from .entity import NestoEntity
from .firmware import Asset, FirmwareRelease, FirmwareReleases, async_get_releases

_LOGGER = logging.getLogger(__name__)

# One install at a time per panel; the entity says when one is running.
PARALLEL_UPDATES = 0

INSTALL_DOCS = (
    "https://github.com/jorgediez/nesto-firmware/blob/main/docs/INSTALL.md#updating"
)
NO_OTA_SUMMARY = (
    "This panel's firmware can't take updates from Home Assistant: install this "
    "release over SSH, as INSTALL.md describes. From 0.2.0 on, it can."
)
# The panel's refusals (section 19), each with its own message.
REFUSALS = {
    "unsupported": "firmware_unsupported",
    "downgrade": "firmware_downgrade",
    "bad_signature": "firmware_bad_signature",
    "too_large": "firmware_too_large",
    "no_space": "firmware_no_space",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the panel's firmware entity."""
    async_add_entities(
        [NestoFirmwareUpdate(entry.runtime_data, async_get_releases(hass))]
    )


class NestoFirmwareUpdate(NestoEntity, UpdateEntity):
    """Nesto on the panel, and the latest release for it."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: NestoCoordinator, releases: FirmwareReleases
    ) -> None:
        """Initialise from the panel and the shared release check."""
        super().__init__(coordinator, "firmware")
        self._releases = releases

    async def async_added_to_hass(self) -> None:
        """Follow the release check too, and run it if nothing has yet."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._releases.async_add_listener(self._handle_releases_update)
        )
        if self._releases.data is None:
            self.coordinator.config_entry.async_create_background_task(
                self.hass,
                self._releases.async_refresh(),
                name=f"{DOMAIN} firmware check",
            )

    @callback
    def _handle_releases_update(self) -> None:
        self.async_write_ha_state()

    # What runs, and what's out ----------------------------------------------

    @property
    def _ota(self) -> bool:
        hello = self.coordinator.hello
        return hello is not None and hello.caps.get("ota") is True

    @property
    def _release(self) -> FirmwareRelease | None:
        """Return the latest release, when it has a program for this panel."""
        release, hello = self._releases.data, self.coordinator.hello
        if release is None or hello is None or release.program(hello.model) is None:
            return None
        return release

    @property
    def available(self) -> bool:
        """Stay available while installing: the panel restarts on the way."""
        return bool(self._attr_in_progress) or super().available

    @property
    def supported_features(self) -> UpdateEntityFeature:
        """Offer to install only on a panel that takes updates."""
        features = UpdateEntityFeature.RELEASE_NOTES
        if self._ota:
            features |= UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
        return features

    @property
    def installed_version(self) -> str | None:
        """Return the version the panel runs."""
        hello = self.coordinator.hello
        return hello.fw if hello and hello.fw else None

    @property
    def latest_version(self) -> str | None:
        """Return the latest release's version, or the panel's without one."""
        if self._releases.data is None:
            return None
        if (release := self._release) is None:
            return self.installed_version
        return release.version

    @property
    def release_url(self) -> str | None:
        """Return the release's page."""
        release = self._release
        return release.url if release and release.url else None

    @property
    def release_summary(self) -> str | None:
        """Say how to install it when the panel can't from here."""
        return None if self._ota else NO_OTA_SUMMARY

    async def async_release_notes(self) -> str | None:
        """Return the release's notes, as GitHub has them."""
        release = self._release
        if release is None:
            return None
        if self._ota:
            return release.notes
        return f"{release.notes}\n\n[Updating over SSH]({INSTALL_DOCS})"

    # Installing -------------------------------------------------------------

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Send the latest release to the panel, and wait for it to run it."""
        release, hello = self._release, self.coordinator.hello
        files = release.program(hello.model) if release and hello else None
        if release is None or hello is None or files is None or not self._ota:
            raise self._error("firmware_none")
        program_asset, signature_asset = files
        if program_asset.size > FIRMWARE_MAX_SIZE:
            raise self._error("firmware_too_large")
        self._attr_in_progress = True
        self._attr_update_percentage = None
        self.async_write_ha_state()
        try:
            signature = (await self._download(signature_asset)).decode().strip()
            program = await self._download(program_asset)
            await self._send(release.version, hello.model, program, signature)
            await self._wait_for_restart(release.version)
        finally:
            self._attr_in_progress = False
            self._attr_update_percentage = None
            self.async_write_ha_state()

    async def _download(self, asset: Asset) -> bytes:
        session = async_get_clientsession(self.hass)
        data = bytearray()
        try:
            async with session.get(
                asset.url,
                timeout=aiohttp.ClientTimeout(total=FIRMWARE_DOWNLOAD_TIMEOUT),
            ) as response:
                response.raise_for_status()
                async for piece in response.content.iter_chunked(FIRMWARE_CHUNK):
                    data += piece
                    if len(data) > FIRMWARE_MAX_SIZE:
                        raise self._error("firmware_too_large")
        except (TimeoutError, aiohttp.ClientError) as err:
            raise self._error(
                "firmware_download", error=str(err) or type(err).__name__
            ) from err
        if len(data) != asset.size:
            raise self._error(
                "firmware_download",
                error=f"got {len(data)} bytes of {asset.size}",
            )
        return bytes(data)

    async def _send(
        self, version: str, model: str, program: bytes, signature: str
    ) -> None:
        """Send the program in pieces, then have the panel put it in place."""
        await self._request(
            {
                "t": "ota_begin",
                "model": model,
                "version": version,
                "size": len(program),
                "sha512": hashlib.sha512(program).hexdigest(),
                "signature": signature,
            }
        )
        for offset in range(0, len(program), FIRMWARE_CHUNK):
            piece = program[offset : offset + FIRMWARE_CHUNK]
            await self._request(
                {"t": "ota_chunk", "offset": offset, "data": b64(piece)}
            )
            self._attr_update_percentage = round(
                100 * (offset + len(piece)) / len(program)
            )
            self.async_write_ha_state()
        await self._request({"t": "ota_end"})
        self._attr_update_percentage = None
        self.async_write_ha_state()
        await self._request({"t": "ota_apply"})
        _LOGGER.info(
            "Panel %s put Nesto %s in place; it restarts with it",
            self.coordinator.config_entry.title,
            version,
        )

    async def _request(self, message: dict[str, Any]) -> None:
        client = self.coordinator.client
        if client is None:
            raise self._error("cannot_send", error="not connected")
        try:
            await client.request(message)
        except PanelError as err:
            if (key := REFUSALS.get(err.code)) is not None:
                raise self._error(key) from err
            raise self._error("cannot_send", error=str(err)) from err
        except NestoError as err:
            raise self._error("cannot_send", error=str(err)) from err

    async def _wait_for_restart(self, version: str) -> None:
        """Wait for the panel to come back, and check what it runs."""
        before = self.coordinator.hello
        back = asyncio.Event()

        @callback
        def check() -> None:
            if self.coordinator.data.connected and self.coordinator.hello is not before:
                back.set()

        remove = self.coordinator.async_add_listener(check)
        try:
            async with asyncio.timeout(FIRMWARE_RESTART_TIMEOUT):
                await back.wait()
        except TimeoutError as err:
            raise self._error("firmware_no_restart", version=version) from err
        finally:
            remove()
        running = self.installed_version
        if running != version:
            raise self._error(
                "firmware_rolled_back", version=version, running=str(running)
            )

    def _error(self, key: str, **placeholders: str) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={
                "name": self.coordinator.config_entry.title,
                **placeholders,
            },
        )
