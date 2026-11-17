"""Config and options flows for Nesto Panel.

The config flow finds a panel, pairs with it, pairs again, or changes its
address; the options flow chooses what the panel shows.

Pairing (PROTOCOL.md section 6) happens over one connection that stays open
between steps: Home Assistant starts it, the panel shows a six-digit code, and
the user types that code here. The code is computed on both sides and never
sent, so a mismatch means someone sits between the two, or a typo; either
way the pairing is cancelled and a new one starts, with a new code.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow, section
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import (
    AuthenticationFailed,
    Hello,
    InsecurePanel,
    NestoClient,
    NestoError,
    PanelError,
    UnsupportedProtocol,
)
from .const import (
    CONF_ALL_LIGHTS,
    CONF_AREA,
    CONF_ENTITIES,
    CONF_KEY,
    CONF_MODE,
    CONF_PANEL_ID,
    CONF_REMOVED,
    DEFAULT_PORT,
    DOMAIN,
    MODE_MANUAL,
    MODE_ROOM,
    panel_title,
)
from .room import (
    SECTIONS,
    panel_area,
    removed_from_area,
    section_domains,
    section_lists,
)

_LOGGER = logging.getLogger(__name__)

CONF_CODE = "code"

ADDRESS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    }
)
CODE_SCHEMA = vol.Schema({vol.Required(CONF_CODE): cv.string})


class NestoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a panel, pair it again, or change its address."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise with nothing known about the panel."""
        self._host = ""
        self._port = DEFAULT_PORT
        self._panel_id = ""
        self._client: NestoClient | None = None
        self._code: str | None = None

    # Finding the panel -------------------------------------------------------

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a panel announcing itself over mDNS (section 2)."""
        panel_id = discovery_info.properties.get("id")
        if not panel_id:
            return self.async_abort(reason="not_a_panel")
        host, port = discovery_info.host, discovery_info.port or DEFAULT_PORT
        await self.async_set_unique_id(panel_id)
        # A known panel at a new address: follow it, as section 2 asks.
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
        self._host, self._port = host, port
        self.context["title_placeholders"] = {"name": panel_title(panel_id)}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before pairing a discovered panel."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := await self._async_begin_pairing():
                errors["base"] = error
            else:
                return await self.async_step_pair()
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders=self.context["title_placeholders"],
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a panel's address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host, self._port = user_input[CONF_HOST], user_input[CONF_PORT]
            if error := await self._async_begin_pairing():
                errors["base"] = error
            else:
                return await self.async_step_pair()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                ADDRESS_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    # Pairing ---------------------------------------------------------------

    async def _async_begin_pairing(self) -> str | None:
        """Connect and start pairing; return an error key, or None."""
        client = NestoClient(self._host, self._port)
        try:
            hello = await client.connect()
        except UnsupportedProtocol:
            return "unsupported_protocol"
        except InsecurePanel:
            return "insecure"
        except NestoError as err:
            _LOGGER.debug("Can't reach %s:%s: %s", self._host, self._port, err)
            return "cannot_connect"
        try:
            await self._async_check_panel(hello)
            self._code = await client.begin_pairing()
        except AbortFlow:
            await client.close()
            raise
        except PanelError as err:
            await client.close()
            return (
                "pairing_closed" if err.code == "pairing_closed" else "cannot_connect"
            )
        except NestoError as err:
            await client.close()
            _LOGGER.debug("Pairing with %s failed: %s", self._host, err)
            return "cannot_connect"
        self._panel_id, self._client = hello.panel_id, client
        return None

    async def _async_check_panel(self, hello: Hello) -> None:
        """Make sure the panel is the one this flow is about."""
        if self.source == SOURCE_REAUTH:
            if hello.panel_id != self._get_reauth_entry().unique_id:
                raise AbortFlow("wrong_device")
            return
        await self.async_set_unique_id(hello.panel_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self._host, CONF_PORT: self._port}
        )
        self.context["title_placeholders"] = {"name": panel_title(hello.panel_id)}

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the code the panel shows, and finish pairing."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._client is None:
                # The last attempt failed and closed: start a new one.
                if error := await self._async_begin_pairing():
                    errors["base"] = error
            elif user_input[CONF_CODE].strip() != self._code:
                await self._client.cancel_pairing()
                self._client = None
                errors["base"] = await self._async_begin_pairing() or "wrong_code"
            else:
                client, self._client = self._client, None
                try:
                    key = await client.confirm_pairing()
                except AuthenticationFailed:
                    errors["base"] = "pairing_failed"
                except NestoError as err:
                    _LOGGER.debug("Pairing with %s failed: %s", self._host, err)
                    errors["base"] = "pairing_timeout"
                else:
                    return self._async_finish(key)
        return self.async_show_form(
            step_id="pair",
            data_schema=CODE_SCHEMA,
            description_placeholders=self.context.get("title_placeholders"),
            errors=errors,
        )

    def _async_finish(self, key: bytes) -> ConfigFlowResult:
        data = {CONF_HOST: self._host, CONF_PORT: self._port, CONF_KEY: key.hex()}
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        return self.async_create_entry(
            title=panel_title(self._panel_id),
            data={**data, CONF_PANEL_ID: self._panel_id},
        )

    # Pairing again -----------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Pair again, when the panel no longer accepts the key."""
        self._host, self._port = entry_data[CONF_HOST], entry_data[CONF_PORT]
        self.context["title_placeholders"] = {"name": self._get_reauth_entry().title}
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain how to open pairing on the panel, then start."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := await self._async_begin_pairing():
                errors["base"] = error
            else:
                return await self.async_step_pair()
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders=self.context["title_placeholders"],
            errors=errors,
        )

    # A new address ---------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change a panel's address, checking it's the same panel."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST], user_input[CONF_PORT]
            client = NestoClient(host, port)
            try:
                hello = await client.connect()
            except NestoError as err:
                _LOGGER.debug("Can't reach %s:%s: %s", host, port, err)
                errors["base"] = "cannot_connect"
            else:
                await client.close()
                await self.async_set_unique_id(hello.panel_id)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host, CONF_PORT: port}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                ADDRESS_SCHEMA, user_input or entry.data
            ),
            errors=errors,
        )

    @callback
    def async_remove(self) -> None:
        """Close a pairing left open when the flow goes away."""
        if self._client is not None:
            client, self._client = self._client, None
            self.hass.async_create_task(client.close(), eager_start=True)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NestoOptionsFlow:
        """Return the options flow: what the panel shows."""
        return NestoOptionsFlow()


class NestoOptionsFlow(OptionsFlowWithReload):
    """Choose what the panel shows, section by section, in order.

    Both modes end in the same form: one ordered list per section. Room mode
    fills it from the area the panel's device is in; manual mode starts empty.
    Saving reloads the entry, which connects again and sends the new layout.
    The entry has no update listener, which reauth and reconfigure rule out.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose room or manual mode."""
        if user_input is not None:
            if user_input[CONF_MODE] == MODE_MANUAL:
                return await self.async_step_manual()
            return await self.async_step_room()
        schema = vol.Schema(
            {
                vol.Required(CONF_MODE): SelectSelector(
                    SelectSelectorConfig(
                        options=[MODE_ROOM, MODE_MANUAL],
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_MODE,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_MODE: self.config_entry.options.get(CONF_MODE, MODE_ROOM)}
            ),
        )

    async def async_step_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Order the area's entities, take some off, or add others."""
        entry = self.config_entry
        area = panel_area(self.hass, entry.entry_id, entry.unique_id or "")
        if user_input is not None:
            lists = _lists_from(user_input)
            return self.async_create_entry(
                data={
                    CONF_MODE: MODE_ROOM,
                    **lists,
                    CONF_ALL_LIGHTS: _all_lights_from(user_input),
                    CONF_AREA: area.id if area is not None else None,
                    CONF_REMOVED: removed_from_area(self.hass, area, lists),
                }
            )
        # What the panel shows now, or would in room mode.
        lists = section_lists(self.hass, {**entry.options, CONF_MODE: MODE_ROOM}, area)
        return self._show_sections(
            "room" if area is not None else "room_no_area",
            lists,
            {"area": area.name if area is not None else ""},
        )

    async_step_room_no_area = async_step_room

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the entities to show, from anywhere, in order."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_MODE: MODE_MANUAL,
                    **_lists_from(user_input),
                    CONF_ALL_LIGHTS: _all_lights_from(user_input),
                }
            )
        options = self.config_entry.options
        if options.get(CONF_MODE) == MODE_MANUAL:
            lists = section_lists(self.hass, options, None)
        else:
            lists = {section: [] for section in SECTIONS}
        return self._show_sections("manual", lists, {})

    def _show_sections(
        self, step_id: str, lists: Mapping[str, list[str]], placeholders: dict[str, str]
    ) -> ConfigFlowResult:
        """Show one ordered list per section; the empty ones start folded."""
        fields: dict[Any, Any] = {}
        suggested: dict[str, dict[str, Any]] = {}
        for name in SECTIONS:
            inner: dict[Any, Any] = {
                vol.Optional(CONF_ENTITIES): EntitySelector(
                    EntitySelectorConfig(
                        multiple=True, reorder=True, domain=section_domains(name)
                    )
                )
            }
            suggested[name] = {CONF_ENTITIES: lists[name]}
            if name == "lights":
                inner[vol.Optional(CONF_ALL_LIGHTS)] = BooleanSelector()
                suggested[name][CONF_ALL_LIGHTS] = self.config_entry.options.get(
                    CONF_ALL_LIGHTS, False
                )
            fields[vol.Required(name)] = section(
                vol.Schema(inner),
                {"collapsed": name != "lights" and not lists[name]},
            )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), suggested
            ),
            description_placeholders=placeholders,
        )


def _lists_from(user_input: Mapping[str, Any]) -> dict[str, list[str]]:
    """Return each section's entities as the form gave them, in order."""
    return {
        name: list(dict.fromkeys(user_input.get(name, {}).get(CONF_ENTITIES, [])))
        for name in SECTIONS
    }


def _all_lights_from(user_input: Mapping[str, Any]) -> bool:
    return bool(user_input.get("lights", {}).get(CONF_ALL_LIGHTS, False))
