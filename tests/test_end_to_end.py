"""The integration with the real client, against the pretend panel."""

from __future__ import annotations

import pytest

from custom_components.nesto.const import CONF_KEY, CONF_PANEL_ID, DOMAIN
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .fake_panel import PANEL_ID, FakePanel

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture
async def panel():
    panel = FakePanel()
    await panel.start()
    yield panel
    await panel.stop()


async def test_pair_connect_identify_remove(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: panel.port}
    )
    assert result["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"code": panel.code}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.data[CONF_KEY] == panel.key.hex()
    assert entry.data[CONF_PANEL_ID] == PANEL_ID
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    # Synchronised (section 8), then ready.
    for kind in ("time", "config", "layout", "states"):
        await panel.wait_for(kind)
    await panel.send({"t": "ready"})
    for _ in range(10):
        await hass.async_block_till_done()
        if entry.runtime_data.data.ready:
            break
    assert entry.runtime_data.data.ready

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.nesto_panel_0a1b2c_identify"},
        blocking=True,
    )
    await panel.wait_for("identify")

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    await panel.wait_for("unpair")
