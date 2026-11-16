"""The connection to one panel, kept open, and what it reports.

The panel pushes everything over one connection (PROTOCOL.md section 1), so
nothing is polled: this coordinator connects, synchronises (section 8), passes
on what the panel sends, and connects again when the connection drops.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
    async_track_utc_time_change,
)
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
from .notifications import EVENTS as NOTIFICATION_EVENTS
from .room import (
    IntentError,
    Keys,
    Layout,
    async_handle_intent,
    build_layout,
    diff,
    item_state,
)
from .settings import LOCAL, Path, Settings

_LOGGER = logging.getLogger(__name__)

type NestoConfigEntry = ConfigEntry[NestoCoordinator]


class NotPaired(AuthenticationFailed):
    """The panel says it holds no pairing key: someone chose Forget on it."""


class WrongPanel(CannotConnect):
    """Another panel answers at the address: the panel's address changed."""


# The display's states (section 13).
DISPLAY_STATES = ("active", "standby", "off")

# A `sensors` message's fields and their types (section 17).
SENSOR_FIELDS: dict[str, type] = {
    "temperature_c": float,
    "humidity_pct": float,
    "illuminance_raw": int,
    "motion_near": bool,
    "motion_far": bool,
    "supply_v": float,
    "usb_v": float,
    "battery_v": float,
}
# Press gestures and where they happen (section 17).
GESTURES = ("single", "double", "long")
CONTEXTS = ("ui", "standby", "off")
# How often to ask the panel how it stands, for the status entities.
STATUS_INTERVAL = timedelta(minutes=5)
# A restart time that moves less than this is the same restart: uptime is
# counted in whole seconds, and the answer takes a moment to arrive.
RESTART_TOLERANCE = timedelta(minutes=1)


def signal_input(entry_id: str) -> str:
    """Return the dispatcher signal for a panel's press gestures."""
    return f"{DOMAIN}_{entry_id}_input"


def signal_notification(entry_id: str) -> str:
    """Return the dispatcher signal for what happens to notifications."""
    return f"{DOMAIN}_{entry_id}_notification"


@dataclass(frozen=True)
class DisplayState:
    """Where the panel's display stands, as it last reported."""

    state: str  # one of DISPLAY_STATES
    brightness: int  # 0 to 100, as the screen shows it now


@dataclass(frozen=True)
class PanelStatus:
    """How the panel stands, from its last `info` answer (section 18)."""

    last_restart: datetime | None = None
    free_ram_kb: int | None = None
    ip: str | None = None
    base_connected: bool | None = None
    base_fw: str | None = None


@dataclass(frozen=True)
class PanelData:
    """What the coordinator knows about its panel."""

    connected: bool = False
    # The panel has its time, settings, layout and states (section 8).
    ready: bool = False
    # None until the panel reports it, right after synchronising.
    display: DisplayState | None = None
    # The last `sensors` message's readings; a sensor that isn't working is
    # left out.
    sensors: dict[str, Any] = field(default_factory=dict)
    status: PanelStatus | None = None


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
        # What the panel shows, as last sent, and the states it knows.
        self._keys = Keys()
        self.layout: Layout | None = None
        self._known: dict[str, dict[str, Any]] = {}
        self._send_lock = asyncio.Lock()
        self._room_changed = False
        self._room_task: asyncio.Task[None] | None = None
        self._tracked: frozenset[str] = frozenset()
        self._untrack_states: CALLBACK_TYPE | None = None
        self._left_out: tuple[str, ...] = ()
        self.settings = Settings(hass, entry.entry_id)
        # The sensors the panel had when its entities were set up.
        self.sensor_caps: frozenset[str] | None = None

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
        self._check_sensor_caps(hello)
        self._update_device(hello)
        if self._was_connected:
            _LOGGER.info("Panel %s is back", entry.title)
        self._was_connected = True
        self.async_set_updated_data(PanelData(connected=True))

    async def _async_synchronise(self) -> None:
        """Send what the panel shows, in the order section 8 asks for."""
        await self.async_send_time()
        await self._async_send(self.settings.as_config())
        async with self._send_lock:
            await self._async_send_room(self.build_layout())

    # Sensors, status and gestures --------------------------------------------

    @callback
    def _check_sensor_caps(self, hello: Hello) -> None:
        """Reload when the panel's sensors changed, to add or drop entities.

        A Nest Gen 2 lists its base's sensors only while it has a base.
        """
        sensors = hello.caps.get("sensors")
        caps = frozenset(sensors) if isinstance(sensors, list) else frozenset()
        if self.sensor_caps is None:
            self.sensor_caps = caps
        elif caps != self.sensor_caps:
            _LOGGER.info(
                "Panel %s has other sensors now; reloading", self.config_entry.title
            )
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    @callback
    def _sensors(self, message: dict[str, Any]) -> None:
        readings: dict[str, Any] = {}
        for name, kind in SENSOR_FIELDS.items():
            value = message.get(name)
            if kind is bool:
                if isinstance(value, bool):
                    readings[name] = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                readings[name] = kind(value)
        self.async_set_updated_data(replace(self.data, sensors=readings))

    @callback
    def _input(self, message: dict[str, Any]) -> None:
        """Pass a press gesture on, once the panel is ready (section 8)."""
        gesture, context = message.get("gesture"), message.get("context")
        if not self.data.ready or gesture not in GESTURES or context not in CONTEXTS:
            return
        async_dispatcher_send(
            self.hass, signal_input(self.config_entry.entry_id), gesture, context
        )

    @callback
    def _notify_event(self, message: dict[str, Any]) -> None:
        """Pass on what happened to a notification, once ready (section 14)."""
        event, nid, action = (
            message.get("event"),
            message.get("nid"),
            message.get("action"),
        )
        if (
            not self.data.ready
            or event not in NOTIFICATION_EVENTS
            or not isinstance(nid, str)
            or (event == "action") != isinstance(action, str)
        ):
            return
        async_dispatcher_send(
            self.hass,
            signal_notification(self.config_entry.entry_id),
            event,
            nid,
            action,
        )

    async def async_notify(self, request: dict[str, Any]) -> None:
        """Show a notification on the panel, or raise why not."""
        await self.async_request(request)

    async def async_clear_notifications(self, nid: str | None) -> None:
        """Remove one notification, or all of them."""
        message: dict[str, Any] = {"t": "notify_clear"}
        if nid is None:
            message["all"] = True
        else:
            message["nid"] = nid
        try:
            await self._async_send(message)
        except NestoError as err:
            raise self._cannot_send(err) from err

    async def _async_refresh_status(self, _now: datetime | None = None) -> None:
        """Ask the panel how it stands, for the status entities."""
        client = self.client
        if client is None or not self.data.ready:
            return
        try:
            info = await client.request({"t": "info"})
        except NestoError as err:
            _LOGGER.debug("Couldn't ask the panel how it stands: %s", err)
            return
        if not self.data.connected:
            return
        self.async_set_updated_data(
            replace(self.data, status=self._status(info, self.data.status))
        )

    @staticmethod
    def _status(info: dict[str, Any], last: PanelStatus | None) -> PanelStatus:
        uptime = info.get("uptime_s")
        restart: datetime | None = None
        if isinstance(uptime, (int, float)) and not isinstance(uptime, bool):
            restart = dt_util.utcnow().replace(microsecond=0) - timedelta(
                seconds=uptime
            )
            if (
                last is not None
                and last.last_restart is not None
                and abs(restart - last.last_restart) < RESTART_TOLERANCE
            ):
                restart = last.last_restart

        def typed(name: str, kind: type) -> Any:
            value = info.get(name)
            if isinstance(value, kind) and (
                kind is bool or not isinstance(value, bool)
            ):
                return value
            return None

        return PanelStatus(
            last_restart=restart,
            free_ram_kb=typed("free_ram_kb", int),
            ip=typed("ip", str),
            base_connected=typed("base_connected", bool),
            base_fw=typed("base_fw", str),
        )

    # Settings and the display --------------------------------------------------

    async def async_set_setting(self, path: Path, value: Any) -> None:
        """Change a setting, save it, and send the panel its settings.

        A panel that isn't connected gets them when it connects again.
        """
        if not self.settings.set(path, value):
            return
        self.async_update_listeners()
        if not self.connected or path[0] == LOCAL:
            return
        try:
            await self._async_send(self.settings.as_config())
        except NestoError as err:
            # A broken connection reports itself through on_close, and the
            # settings go with the next synchronisation.
            _LOGGER.debug("Couldn't send the settings: %s", err)

    async def async_set_display(self, state: str) -> None:
        """Turn the display active, to standby or off (section 13)."""
        await self.async_request({"t": "display", "state": state})

    @callback
    def _display_state(self, message: dict[str, Any]) -> None:
        state, brightness = message.get("state"), message.get("brightness")
        if state not in DISPLAY_STATES or not isinstance(brightness, int):
            _LOGGER.debug("Panel %s sent a bad display_state", self.config_entry.title)
            return
        self.async_set_updated_data(
            replace(self.data, display=DisplayState(state, brightness))
        )

    # The room --------------------------------------------------------------------

    def build_layout(self) -> Layout:
        """Return what the panel should show now."""
        entry = self.config_entry
        layout = build_layout(self.hass, entry, self.panel_id, self._keys)
        if layout.left_out != self._left_out:
            self._left_out = layout.left_out
            if layout.left_out:
                _LOGGER.warning(
                    "Panel %s shows 48 items at most; left out: %s",
                    entry.title,
                    ", ".join(layout.left_out),
                )
        return layout

    async def _async_send_room(self, layout: Layout) -> None:
        """Send a layout and every item's state (sections 9 and 10)."""
        states = {item.key: item_state(self.hass, item) for item in layout.items}
        await self._async_send(layout.as_message(self._next_layout_rev()))
        self.layout = layout
        self._known = {}
        await self._async_send({"t": "states", "full": True, "items": states})
        self._known = states
        self._track(layout)

    @callback
    def _track(self, layout: Layout) -> None:
        """Follow the states of what the panel shows, or waits to show."""
        wanted = frozenset(layout.entity_ids | layout.waiting)
        if wanted == self._tracked:
            return
        self._untrack()
        self._tracked = wanted
        if wanted:
            self._untrack_states = async_track_state_change_event(
                self.hass, wanted, self._async_room_changed
            )

    @callback
    def _untrack(self) -> None:
        if self._untrack_states is not None:
            self._untrack_states()
            self._untrack_states = None
        self._tracked = frozenset()

    @callback
    def _async_room_changed(self, _event: Event | None = None) -> None:
        """Note that the room changed, and send what changed, soon.

        Changes coming together are sent together: the sender builds the room
        when it runs, from what is current then.
        """
        self._room_changed = True
        if self._room_task is None or self._room_task.done():
            self._room_task = self.config_entry.async_create_background_task(
                self.hass, self._async_send_changes(), name=f"{DOMAIN} room"
            )

    async def _async_send_changes(self) -> None:
        async with self._send_lock:
            while self._room_changed:
                self._room_changed = False
                if not self.connected or self.layout is None:
                    return
                try:
                    await self._async_send_room_changes(self.layout)
                except NestoError as err:
                    # A broken connection reports itself through on_close.
                    _LOGGER.debug("Couldn't update the panel: %s", err)
                    return

    async def _async_send_room_changes(self, sent: Layout) -> None:
        """Send a new layout if it changed, else the states that changed."""
        layout = self.build_layout()
        if (layout.title, layout.items) != (sent.title, sent.items):
            await self._async_send_room(layout)
            return
        # The same items; what waits for a state may differ.
        self.layout = layout
        self._track(layout)
        changes: dict[str, dict[str, Any]] = {}
        for item in layout.items:
            known = self._known.setdefault(item.key, {})
            if changed := diff(known, item_state(self.hass, item)):
                changes[item.key] = changed
                known.update(changed)
        if changes:
            await self._async_send({"t": "states", "full": False, "items": changes})

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
        """Send the time every hour, and follow changes to the room.

        The hourly time also catches daylight saving. The registries say when
        entities join or leave the room, or the panel's device moves.
        """
        entry = self.config_entry
        entry.async_on_unload(
            async_track_utc_time_change(
                self.hass, self._async_hourly, minute=0, second=0
            )
        )
        for event_type in (
            ar.EVENT_AREA_REGISTRY_UPDATED,
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            er.EVENT_ENTITY_REGISTRY_UPDATED,
        ):
            entry.async_on_unload(
                self.hass.bus.async_listen(event_type, self._async_room_changed)
            )
        entry.async_on_unload(self._untrack)
        entry.async_on_unload(
            async_track_time_interval(
                self.hass,
                self._async_refresh_status,
                STATUS_INTERVAL,
                name=f"{DOMAIN} status",
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
            raise self._cannot_send(err) from err

    def _cannot_send(self, err: NestoError) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_send",
            translation_placeholders={
                "name": self.config_entry.title,
                "error": str(err),
            },
        )

    @callback
    def _on_message(self, message: dict[str, Any]) -> None:
        kind = message["t"]
        if kind == "intent" and "id" in message and self.client is not None:
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_intent(self.client, message),
                name=f"{DOMAIN} intent",
            )
        elif "id" in message:
            self._answer(message)
        elif kind == "ready":
            _LOGGER.debug("Panel %s is ready", self.config_entry.title)
            self.async_set_updated_data(replace(self.data, ready=True))
            self.config_entry.async_create_background_task(
                self.hass, self._async_refresh_status(), name=f"{DOMAIN} status"
            )
        elif kind == "display_state":
            self._display_state(message)
        elif kind == "sensors":
            self._sensors(message)
        elif kind == "input":
            self._input(message)
        elif kind == "notify_event":
            self._notify_event(message)
        else:
            _LOGGER.debug("Panel %s sent %s", self.config_entry.title, kind)

    def _answer(self, request: dict[str, Any]) -> None:
        """Answer the panel's requests that nothing here handles yet."""
        kind = request["t"]
        if kind == "image_next":
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

    async def _async_intent(self, client: NestoClient, request: dict[str, Any]) -> None:
        """Carry out an intent, and answer once the service call returns."""
        try:
            if self.layout is None:
                raise IntentError("unknown_key", f"No item {request.get('key')}")
            await async_handle_intent(self.hass, self.layout, request)
            _LOGGER.debug(
                "Panel %s: %s on %s",
                self.config_entry.title,
                request.get("action"),
                request.get("key"),
            )
        except IntentError as err:
            _LOGGER.debug("Panel %s: intent failed: %s", self.config_entry.title, err)
            await self._async_respond(client, request["id"], err.code, str(err))
            return
        try:
            await client.respond(request["id"])
        except NestoError as err:
            _LOGGER.debug("Couldn't answer the panel: %s", err)

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
