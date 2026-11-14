"""The connection to one panel, kept open, and what it reports.

The panel pushes everything over one connection (PROTOCOL.md section 1), so
nothing is polled: this coordinator connects, synchronises (section 8), passes
on what the panel sends, and connects again when the connection drops.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.event import async_track_utc_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .client import (
    AuthenticationFailed,
    CannotConnect,
    Hello,
    NestoClient,
    NestoError,
    UnsupportedProtocol,
)
from .const import CONF_KEY, CONF_PANEL_ID, DOMAIN, RECONNECT_DELAYS

_LOGGER = logging.getLogger(__name__)

type NestoConfigEntry = ConfigEntry[NestoCoordinator]


class NotPaired(AuthenticationFailed):
    """The panel says it holds no pairing key: someone chose Forget on it."""


class WrongPanel(CannotConnect):
    """Another panel answers at the address: the panel's address changed."""


@dataclass(frozen=True)
class PanelData:
    """What the coordinator knows about its panel."""

    connected: bool = False
    # The panel has its time, settings, layout and states (section 8).
    ready: bool = False


class NestoCoordinator(DataUpdateCoordinator[PanelData]):
    """Keep one panel connected and synchronised."""

    config_entry: NestoConfigEntry

    def __init__(self, hass: HomeAssistant, entry: NestoConfigEntry) -> None:
        """Initialise; nothing connects until `async_connect`."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=None,  # pushed, never polled
        )
        self.client: NestoClient | None = None
        self.hello: Hello | None = None
        self.last_error: Exception | None = None
        self._layout_rev = 0
        self._reconnect_task: asyncio.Task[None] | None = None
        self._was_connected = False
        self._shutting_down = False
        self.data = PanelData()

    @property
    def panel_id(self) -> str:
        """Return the panel id, the entry's unique id."""
        return self.config_entry.unique_id or self.config_entry.data[CONF_PANEL_ID]

    @property
    def connected(self) -> bool:
        """Return whether the session is open."""
        return self.client is not None and self.client.authenticated

    async def _async_update_data(self) -> PanelData:
        """Return what is known: the panel pushes, so there is nothing to fetch."""
        return self.data

    # Connecting ----------------------------------------------------------------

    async def async_connect(self) -> None:
        """Connect, authenticate and synchronise, or raise why not."""
        entry = self.config_entry
        client = NestoClient(
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            on_message=self._on_message,
            on_close=self._on_close,
        )
        hello = await client.connect()
        try:
            if hello.panel_id != self.panel_id:
                raise WrongPanel(
                    f"{entry.data[CONF_HOST]} is panel {hello.panel_id} now"
                )
            if not hello.paired:
                raise NotPaired("the panel holds no pairing key")
            await client.authenticate(bytes.fromhex(entry.data[CONF_KEY]))
            self.client = client
            self.hello = hello
            await self._async_synchronise()
        except Exception:
            await client.close()
            self.client = None
            raise
        self.last_error = None
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self._update_device(hello)
        if self._was_connected:
            _LOGGER.info("Panel %s is back", entry.title)
        self._was_connected = True
        self.async_set_updated_data(PanelData(connected=True))

    async def _async_synchronise(self) -> None:
        """Send what the panel shows, in the order section 8 asks for."""
        await self.async_send_time()
        # Every setting left out takes the panel's own default; the settings
        # entities fill this in later.
        await self._async_send({"t": "config"})
        await self._async_send(
            {
                "t": "layout",
                "rev": self._next_layout_rev(),
                "title": self.config_entry.title,
                "items": [],
            }
        )
        await self._async_send({"t": "states", "full": True, "items": {}})

    def _next_layout_rev(self) -> int:
        # Increasing for this panel even across restarts of Home Assistant,
        # without storing anything: the clock, nudged past the last one.
        self._layout_rev = max(self._layout_rev + 1, int(time.time()))
        return self._layout_rev

    async def async_send_time(self, _now: datetime | None = None) -> None:
        """Send the time and the UTC offset (section 16), if connected."""
        if not self.connected:
            return
        local = dt_util.now()
        offset = local.utcoffset()
        await self._async_send(
            {
                "t": "time",
                "utc_ms": int(local.timestamp() * 1000),
                "utc_offset_min": int(offset.total_seconds() // 60) if offset else 0,
                "tz": str(self.hass.config.time_zone),
            }
        )

    @callback
    def async_start(self) -> None:
        """Send the time every hour, which also catches daylight saving."""
        self.config_entry.async_on_unload(
            async_track_utc_time_change(
                self.hass, self._async_hourly, minute=0, second=0
            )
        )

    async def _async_hourly(self, now: datetime) -> None:
        try:
            await self.async_send_time(now)
        except NestoError as err:
            # A broken connection reports itself through on_close.
            _LOGGER.debug("Couldn't send the time: %s", err)

    async def async_shutdown(self) -> None:
        """Stop reconnecting and close the connection."""
        self._shutting_down = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self.client is not None:
            await self.client.close()
            self.client = None
        await super().async_shutdown()

    # Talking to the panel --------------------------------------------------------

    async def _async_send(self, message: dict[str, Any]) -> None:
        if self.client is None:
            raise CannotConnect("not connected")
        await self.client.send(message)

    async def async_request(
        self, message: dict[str, Any], seconds: float | None = None
    ) -> dict[str, Any]:
        """Send a request, raising a translated error when it fails."""
        try:
            if self.client is None:
                raise CannotConnect("not connected")
            return await self.client.request(message, seconds)
        except NestoError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_send",
                translation_placeholders={
                    "name": self.config_entry.title,
                    "error": str(err),
                },
            ) from err

    @callback
    def _on_message(self, message: dict[str, Any]) -> None:
        kind = message["t"]
        if "id" in message:
            self._answer(message)
        elif kind == "ready":
            _LOGGER.debug("Panel %s is ready", self.config_entry.title)
            self.async_set_updated_data(PanelData(connected=True, ready=True))
        else:
            _LOGGER.debug("Panel %s sent %s", self.config_entry.title, kind)

    def _answer(self, request: dict[str, Any]) -> None:
        """Answer the panel's requests that nothing here handles yet."""
        kind = request["t"]
        if kind == "intent":
            # The layout has no items yet, so any key is stale (section 11).
            error = ("unknown_key", f"No item {request.get('key')}")
        elif kind == "image_next":
            error = ("no_images", "No carousel images are configured")
        else:
            error = ("unknown_type", f"Unknown request {kind}")
        client = self.client
        if client is None:
            return
        self.config_entry.async_create_background_task(
            self.hass,
            self._async_respond(client, request["id"], *error),
            name=f"{DOMAIN} answer {kind}",
        )

    @staticmethod
    async def _async_respond(
        client: NestoClient, request_id: Any, code: str, message: str
    ) -> None:
        try:
            await client.respond(request_id, code, message)
        except NestoError as err:
            _LOGGER.debug("Couldn't answer the panel: %s", err)

    # Losing the connection ---------------------------------------------------

    @callback
    def _on_close(self, reason: Exception | None) -> None:
        self.client = None
        if self._shutting_down:
            return
        self.last_error = reason
        _LOGGER.warning("Panel %s is unavailable: %s", self.config_entry.title, reason)
        self.async_set_updated_data(PanelData())
        self.async_reconnect()

    @callback
    def async_reconnect(self) -> None:
        """Keep trying to reach the panel, in the background."""
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.config_entry.async_create_background_task(
                self.hass, self._async_reconnect(), name=f"{DOMAIN} reconnect"
            )

    async def _async_reconnect(self) -> None:
        attempt = 0
        while not self._shutting_down:
            await asyncio.sleep(
                RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            )
            attempt += 1
            try:
                await self.async_connect()
            except AuthenticationFailed as err:
                self.last_error = err
                _LOGGER.warning(
                    "Panel %s doesn't accept the pairing key: %s",
                    self.config_entry.title,
                    err,
                )
                self.config_entry.async_start_reauth(self.hass)
                return
            except UnsupportedProtocol as err:
                self.last_error = err
                self.async_report_unsupported(err)
                return
            except NestoError as err:
                self.last_error = err
                _LOGGER.debug(
                    "Panel %s still unreachable: %s", self.config_entry.title, err
                )
            else:
                return

    @callback
    def async_report_unsupported(self, err: UnsupportedProtocol) -> None:
        """Raise a repair issue: one side needs updating (section 22)."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="unsupported_protocol",
            translation_placeholders={
                "name": self.config_entry.title,
                "version": str(err.version),
            },
        )

    @property
    def _issue_id(self) -> str:
        return f"unsupported_protocol_{self.config_entry.entry_id}"

    def _update_device(self, hello: Hello) -> None:
        """Keep the device's firmware version current after an update."""
        registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(
            registry, self.config_entry.entry_id
        ):
            if device.sw_version != hello.fw:
                registry.async_update_device(device.id, sw_version=hello.fw)
