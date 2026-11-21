"""Nesto's firmware releases, as GitHub publishes them.

One check serves every panel: Home Assistant asks GitHub for the latest
published release of nesto-firmware when the first panel's update entity is
added, then every 12 hours while one is enabled. Nothing about the panels
goes with it. A release attaches each model's program and its signature as
`nesto-VERSION-MODEL.bin` and `nesto-VERSION-MODEL.bin.sig` (PROTOCOL.md
section 19); the panel checks the signature itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Final

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util.hass_dict import HassKey

from .const import (
    DOMAIN,
    FIRMWARE_CHECK_INTERVAL,
    FIRMWARE_RELEASES_URL,
    FIRMWARE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

DATA_FIRMWARE: Final[HassKey[FirmwareReleases]] = HassKey(f"{DOMAIN}_firmware")


@dataclass(frozen=True)
class Asset:
    """A file attached to a release."""

    url: str
    size: int


@dataclass(frozen=True)
class FirmwareRelease:
    """The latest release: its version, notes and files."""

    version: str
    url: str
    notes: str
    assets: dict[str, Asset] = field(default_factory=dict)

    def program(self, model: str) -> tuple[Asset, Asset] | None:
        """Return the program and its signature for a model, if it has them."""
        name = f"nesto-{self.version}-{model}.bin"
        program, signature = self.assets.get(name), self.assets.get(f"{name}.sig")
        if program is None or signature is None:
            return None
        return program, signature

    @classmethod
    def from_github(cls, release: dict[str, Any]) -> FirmwareRelease:
        """Return a release from GitHub's answer, or raise ValueError."""
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.removeprefix("v"):
            raise ValueError("the release has no tag")
        assets: dict[str, Asset] = {}
        for asset in release.get("assets") or []:
            name, url, size = (
                asset.get("name"),
                asset.get("browser_download_url"),
                asset.get("size"),
            )
            if isinstance(name, str) and isinstance(url, str) and isinstance(size, int):
                assets[name] = Asset(url, size)
        return cls(
            version=tag.removeprefix("v"),
            url=str(release.get("html_url") or ""),
            notes=str(release.get("body") or ""),
            assets=assets,
        )


class FirmwareReleases(DataUpdateCoordinator[FirmwareRelease | None]):
    """The latest firmware release, checked twice a day for every panel."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise; the first check runs when an update entity is added."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=None,
            name=f"{DOMAIN} firmware",
            update_interval=FIRMWARE_CHECK_INTERVAL,
        )
        self.data = None

    async def _async_update_data(self) -> FirmwareRelease | None:
        """Ask GitHub; on a failure keep the last answer, and try next time."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                FIRMWARE_RELEASES_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=aiohttp.ClientTimeout(total=FIRMWARE_TIMEOUT),
            ) as response:
                response.raise_for_status()
                release = FirmwareRelease.from_github(await response.json())
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            _LOGGER.debug("Couldn't check for Nesto firmware: %s", err)
            return self.data
        _LOGGER.debug("Latest Nesto firmware: %s", release.version)
        return release


def async_get_releases(hass: HomeAssistant) -> FirmwareReleases:
    """Return the one firmware check, made on first use."""
    if (releases := hass.data.get(DATA_FIRMWARE)) is None:
        releases = hass.data[DATA_FIRMWARE] = FirmwareReleases(hass)
    return releases
