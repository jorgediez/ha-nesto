"""The config flow: discovery, adding by address, pairing, reauth, reconfigure."""

from __future__ import annotations

from ipaddress import ip_address
from unittest.mock import patch

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.nesto.client import (
    AuthenticationFailed,
    CannotConnect,
    InsecurePanel,
    PanelError,
    UnsupportedProtocol,
)
from custom_components.nesto.const import CONF_KEY, CONF_PANEL_ID, DOMAIN
from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .common import CODE, HOST, KEY, PANEL_ID, PORT, TITLE, ClientFactory, make_hello

DISCOVERY = ZeroconfServiceInfo(
    ip_address=ip_address("192.168.1.61"),
    ip_addresses=[ip_address("192.168.1.61")],
    hostname="nesto-0a1b2c.local.",
    name="Nesto Panel 0a1b2c._nesto._tcp.local.",
    port=6171,
    type="_nesto._tcp.local.",
    properties={"id": PANEL_ID, "proto": "1", "fw": "0.1.0", "paired": "0"},
)


@pytest.fixture(autouse=True)
def no_setup():
    """Stop at the flow: setting the entry up would connect a client of its own."""
    with patch("custom_components.nesto.async_setup_entry", return_value=True):
        yield


async def start_user(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def submit_address(hass: HomeAssistant, result: dict) -> dict:
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: PORT}
    )


async def submit_code(hass: HomeAssistant, result: dict, code: str = CODE) -> dict:
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"code": code}
    )


# Adding by address -----------------------------------------------------------


async def test_add_by_address(hass: HomeAssistant, clients: ClientFactory) -> None:
    clients.hello = make_hello(paired=False, pairing_open=True)
    result = await submit_address(hass, await start_user(hass))
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pair"
    assert result["description_placeholders"] == {"name": TITLE}

    result = await submit_code(hass, result, f" {CODE} ")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == {
        CONF_HOST: HOST,
        CONF_PORT: PORT,
        CONF_KEY: KEY.hex(),
        CONF_PANEL_ID: PANEL_ID,
    }
    assert result["result"].unique_id == PANEL_ID
    # One connection, open from the code request until the confirmation.
    assert len(clients.clients) == 1
    clients.last.confirm_pairing.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (CannotConnect("refused"), "cannot_connect"),
        (UnsupportedProtocol(2), "unsupported_protocol"),
        (InsecurePanel("dev build"), "insecure"),
    ],
)
async def test_cant_connect(
    hass: HomeAssistant, clients: ClientFactory, error: Exception, key: str
) -> None:
    clients.connect_error = error
    result = await submit_address(hass, await start_user(hass))
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": key}

    clients.connect_error = None
    result = await submit_address(hass, result)
    assert result["step_id"] == "pair"


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (PanelError("pairing_closed"), "pairing_closed"),
        (PanelError("internal"), "cannot_connect"),
        (CannotConnect("closed"), "cannot_connect"),
    ],
)
async def test_pairing_wont_start(
    hass: HomeAssistant, clients: ClientFactory, error: Exception, key: str
) -> None:
    clients.begin_error = error
    result = await submit_address(hass, await start_user(hass))
    assert result["errors"] == {"base": key}
    clients.last.close.assert_awaited()


async def test_already_configured(
    hass: HomeAssistant, clients: ClientFactory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.99", CONF_PORT: 6172}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_HOST] == "192.168.1.99"
    assert config_entry.data[CONF_PORT] == 6172
    clients.last.close.assert_awaited()
    clients.last.begin_pairing.assert_not_awaited()


# Pairing ---------------------------------------------------------------------


async def test_a_wrong_code_starts_again(
    hass: HomeAssistant, clients: ClientFactory
) -> None:
    clients.codes = ["111111", "222222"]
    result = await submit_address(hass, await start_user(hass))

    result = await submit_code(hass, result, "999999")
    assert result["step_id"] == "pair"
    assert result["errors"] == {"base": "wrong_code"}
    first, second = clients.clients
    first.cancel_pairing.assert_awaited_once()
    first.confirm_pairing.assert_not_awaited()

    result = await submit_code(hass, result, "111111")  # the old code no longer
    assert result["errors"] == {"base": "wrong_code"}

    result = await submit_code(hass, result, "222222")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    second.confirm_pairing.assert_not_awaited()  # the third connection paired
    clients.last.confirm_pairing.assert_awaited_once()


async def test_a_wrong_code_when_the_panel_is_gone(
    hass: HomeAssistant, clients: ClientFactory
) -> None:
    result = await submit_address(hass, await start_user(hass))
    clients.connect_error = CannotConnect("gone")
    result = await submit_code(hass, result, "999999")
    assert result["errors"] == {"base": "cannot_connect"}

    clients.connect_error = None
    result = await submit_code(hass, result, "anything")  # starts again
    assert result["step_id"] == "pair"
    assert result["errors"] == {}
    result = await submit_code(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (AuthenticationFailed("rejected"), "pairing_failed"),
        (CannotConnect("timed out"), "pairing_timeout"),
    ],
)
async def test_confirming_fails(
    hass: HomeAssistant, clients: ClientFactory, error: Exception, key: str
) -> None:
    result = await submit_address(hass, await start_user(hass))
    clients.confirm_error = error
    result = await submit_code(hass, result)
    assert result["errors"] == {"base": key}

    clients.confirm_error = None
    result = await submit_code(hass, result, "")  # any submit starts again
    assert result["errors"] == {}
    result = await submit_code(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(clients.clients) == 2


async def test_leaving_the_flow_closes_the_connection(
    hass: HomeAssistant, clients: ClientFactory
) -> None:
    result = await submit_address(hass, await start_user(hass))
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()
    clients.last.close.assert_awaited()


# Discovery -------------------------------------------------------------------


async def test_discovery(hass: HomeAssistant, clients: ClientFactory) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"] == {"name": TITLE}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair"
    assert (clients.last.host, clients.last.port) == ("192.168.1.61", 6171)

    result = await submit_code(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "192.168.1.61"
    assert result["data"][CONF_PORT] == 6171


async def test_discovery_cant_connect(
    hass: HomeAssistant, clients: ClientFactory
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    clients.connect_error = CannotConnect("gone")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "zeroconf_confirm"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_discovery_follows_a_known_panel(
    hass: HomeAssistant, clients: ClientFactory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_HOST] == "192.168.1.61"
    assert config_entry.data[CONF_PORT] == 6171
    assert clients.clients == []  # nothing written to the panel


async def test_discovery_without_an_id(hass: HomeAssistant) -> None:
    info = ZeroconfServiceInfo(
        ip_address=DISCOVERY.ip_address,
        ip_addresses=DISCOVERY.ip_addresses,
        hostname=DISCOVERY.hostname,
        name=DISCOVERY.name,
        port=DISCOVERY.port,
        type=DISCOVERY.type,
        properties={},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=info
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_a_panel"


# Pairing again ---------------------------------------------------------------


async def test_reauth(
    hass: HomeAssistant, clients: ClientFactory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"] == {"name": TITLE}

    clients.begin_error = PanelError("pairing_closed")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "pairing_closed"}

    clients.begin_error = None
    new_key = bytes(32)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair"
    clients.last.confirm_pairing.side_effect = None
    clients.last.confirm_pairing.return_value = new_key
    result = await submit_code(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_KEY] == new_key.hex()


async def test_reauth_finds_another_panel(
    hass: HomeAssistant, clients: ClientFactory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    clients.hello = make_hello(panel_id="aabbccddeeff")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    clients.last.close.assert_awaited()


# A new address ---------------------------------------------------------------


async def test_reconfigure(
    hass: HomeAssistant, clients: ClientFactory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    clients.connect_error = CannotConnect("refused")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.70", CONF_PORT: 6170}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    clients.connect_error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.70", CONF_PORT: 6170}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_HOST] == "192.168.1.70"
    assert config_entry.data[CONF_KEY] == KEY.hex()


async def test_reconfigure_finds_another_panel(
    hass: HomeAssistant, clients: ClientFactory, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    clients.hello = make_hello(panel_id="aabbccddeeff")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.70", CONF_PORT: 6170}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert config_entry.data[CONF_HOST] == HOST
