"""The Firmware update entity: the release check, and installing a release."""

from __future__ import annotations

import base64
from datetime import timedelta
import hashlib
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant.typing import WebSocketGenerator

from custom_components.nesto import coordinator as coordinator_module, update
from custom_components.nesto.client import CannotConnect, PanelError
from custom_components.nesto.const import FIRMWARE_RELEASES_URL
from homeassistant.components.update import (
    ATTR_IN_PROGRESS,
    ATTR_INSTALLED_VERSION,
    ATTR_LATEST_VERSION,
    ATTR_RELEASE_SUMMARY,
    ATTR_RELEASE_URL,
    ATTR_UPDATE_PERCENTAGE,
    DOMAIN as UPDATE_DOMAIN,
    SERVICE_INSTALL,
    UpdateEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .common import ClientFactory, make_hello

RELEASE_PAGE = "https://github.com/jorgediez/nesto-firmware/releases/tag/v0.2.0"
DOWNLOADS = "https://github.com/jorgediez/nesto-firmware/releases/download/v0.2.0"
PROGRAM_URL = f"{DOWNLOADS}/nesto-0.2.0-nest-gen2.bin"
SIGNATURE_URL = f"{PROGRAM_URL}.sig"
# Three pieces: two full ones and a short one.
PROGRAM = bytes(range(256)) * 600
SIGNATURE = base64.b64encode(bytes(64)).decode()
OTA_CAPS = {"beeper": True, "input": ["ring", "press"], "ota": True}


def github_release(
    version: str = "0.2.0", model: str = "nest-gen2", size: int = len(PROGRAM)
) -> dict[str, Any]:
    name = f"nesto-{version}-{model}.bin"
    return {
        "tag_name": f"v{version}",
        "html_url": RELEASE_PAGE,
        "body": "## What's new\n\n- Updates from Home Assistant.",
        "assets": [
            {
                "name": f"nesto-{version}-{model}.tar.gz",
                "browser_download_url": f"{DOWNLOADS}/nesto-{version}-{model}.tar.gz",
                "size": 1000,
            },
            {"name": name, "browser_download_url": PROGRAM_URL, "size": size},
            {
                "name": f"{name}.sig",
                "browser_download_url": SIGNATURE_URL,
                "size": len(SIGNATURE) + 1,
            },
        ],
    }


@pytest.fixture(autouse=True)
def no_waiting():
    """Reconnect at once instead of after the backoff."""
    with patch.object(coordinator_module, "RECONNECT_DELAYS", (0,)):
        yield


@pytest.fixture
def released(github: AiohttpClientMocker) -> AiohttpClientMocker:
    """Publish 0.2.0, with its files."""
    github.clear_requests()
    github.get(FIRMWARE_RELEASES_URL, json=github_release())
    github.get(PROGRAM_URL, content=PROGRAM)
    github.get(SIGNATURE_URL, text=SIGNATURE + "\n")
    return github


@pytest.fixture
def takes_updates(clients: ClientFactory) -> ClientFactory:
    clients.hello = make_hello(caps=OTA_CAPS)
    return clients


def firmware_entity(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        UPDATE_DOMAIN, "nesto", f"{entry.entry_id}_firmware"
    )
    assert entity_id is not None
    return entity_id


def state(hass: HomeAssistant, entry: MockConfigEntry):
    current = hass.states.get(firmware_entity(hass, entry))
    assert current is not None
    return current


async def install(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.services.async_call(
        UPDATE_DOMAIN,
        SERVICE_INSTALL,
        {ATTR_ENTITY_ID: firmware_entity(hass, entry)},
        blocking=True,
    )


class Panel:
    """The panel's side of an update, on the mocked client.

    `restart_with` is the version it comes back with after ota_apply; None
    for one that never comes back.
    """

    def __init__(self, hass: HomeAssistant, clients: ClientFactory) -> None:
        self.hass = hass
        self.clients = clients
        self.messages: list[dict[str, Any]] = []
        self.refuse: dict[str, Exception] = {}
        self.restart_with: str | None = "0.2.0"
        clients.last.request.side_effect = self.request

    async def request(self, message: dict[str, Any], _seconds: float | None = None):
        kind = message["t"]
        if kind in self.refuse:
            raise self.refuse[kind]
        self.messages.append(message)
        if kind == "ota_apply":
            self.hass.loop.call_soon(self.restart)
        return {"t": "result", "ok": True}

    def restart(self) -> None:
        if self.restart_with is None:
            self.clients.connect_error = CannotConnect("refused")
        else:
            self.clients.hello = make_hello(fw=self.restart_with, caps=OTA_CAPS)
        self.clients.drop(CannotConnect("the panel closed the connection"))

    def received(self) -> bytes:
        return b"".join(
            base64.b64decode(m["data"]) for m in self.messages if m["t"] == "ota_chunk"
        )


# What it shows ---------------------------------------------------------------


async def test_unique_id_and_category(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The unique id format is fixed: changing it orphans users' entities."""
    entity = er.async_get(hass).async_get(firmware_entity(hass, init_integration))
    assert entity.unique_id == f"{init_integration.entry_id}_firmware"
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.original_device_class == "firmware"


async def test_no_release_known(
    hass: HomeAssistant, init_integration: MockConfigEntry, github: AiohttpClientMocker
) -> None:
    """GitHub didn't answer: the panel's version, and nothing to compare."""
    current = state(hass, init_integration)
    assert current.state == STATE_UNKNOWN
    assert current.attributes[ATTR_INSTALLED_VERSION] == "0.1.0"
    assert github.call_count == 1


@pytest.mark.usefixtures("released", "takes_updates")
async def test_a_new_release(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    current = state(hass, init_integration)
    assert current.state == STATE_ON
    assert current.attributes[ATTR_INSTALLED_VERSION] == "0.1.0"
    assert current.attributes[ATTR_LATEST_VERSION] == "0.2.0"
    assert current.attributes[ATTR_RELEASE_URL] == RELEASE_PAGE
    assert current.attributes[ATTR_RELEASE_SUMMARY] is None
    features = UpdateEntityFeature(current.attributes[ATTR_SUPPORTED_FEATURES])
    assert UpdateEntityFeature.INSTALL in features
    assert UpdateEntityFeature.PROGRESS in features

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "update/release_notes",
            "entity_id": firmware_entity(hass, init_integration),
        }
    )
    result = await client.receive_json()
    assert result["result"] == "## What's new\n\n- Updates from Home Assistant."


@pytest.mark.usefixtures("released")
async def test_a_panel_without_updates_points_to_ssh(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Firmware 0.1.0 has no caps.ota: shown, but installed over SSH."""
    current = state(hass, init_integration)
    assert current.state == STATE_ON
    features = UpdateEntityFeature(current.attributes[ATTR_SUPPORTED_FEATURES])
    assert UpdateEntityFeature.INSTALL not in features
    assert "over SSH" in current.attributes[ATTR_RELEASE_SUMMARY]
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "update/release_notes",
            "entity_id": firmware_entity(hass, init_integration),
        }
    )
    assert "INSTALL.md#updating" in (await client.receive_json())["result"]
    with pytest.raises(HomeAssistantError):
        await install(hass, init_integration)


@pytest.mark.usefixtures("takes_updates")
async def test_a_release_for_another_model_isnt_offered(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: AiohttpClientMocker
) -> None:
    github.clear_requests()
    github.get(FIRMWARE_RELEASES_URL, json=github_release(model="nest-gen3"))
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    current = state(hass, config_entry)
    assert current.state == STATE_OFF
    assert current.attributes[ATTR_LATEST_VERSION] == "0.1.0"
    with pytest.raises(HomeAssistantError) as caught:
        await install(hass, config_entry)
    assert caught.value.translation_key == "no_update_available"
    await hass.config_entries.async_unload(config_entry.entry_id)


@pytest.mark.usefixtures("released", "takes_updates")
async def test_the_check_keeps_its_last_answer(
    hass: HomeAssistant, init_integration: MockConfigEntry, github: AiohttpClientMocker
) -> None:
    """GitHub failing later leaves the release known, and is tried again."""
    github.clear_requests()
    github.get(FIRMWARE_RELEASES_URL, status=500)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=12, seconds=1))
    await hass.async_block_till_done()
    assert github.call_count == 1  # it asked again
    current = state(hass, init_integration)
    assert current.state == STATE_ON
    assert current.attributes[ATTR_LATEST_VERSION] == "0.2.0"


async def test_a_release_without_a_tag_is_ignored(
    hass: HomeAssistant, config_entry: MockConfigEntry, clients: ClientFactory, github
) -> None:
    github.clear_requests()
    github.get(FIRMWARE_RELEASES_URL, json={"assets": []})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert state(hass, config_entry).state == STATE_UNKNOWN
    await hass.config_entries.async_unload(config_entry.entry_id)


# Installing ------------------------------------------------------------------


@pytest.mark.usefixtures("released", "takes_updates")
async def test_install(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    panel = Panel(hass, clients)
    entity_id = firmware_entity(hass, init_integration)
    seen: list[tuple[str, Any, Any]] = []

    def follow(event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if event.data["entity_id"] != entity_id or new is None:
            return
        seen.append(
            (
                new.state,
                new.attributes.get(ATTR_IN_PROGRESS),
                new.attributes.get(ATTR_UPDATE_PERCENTAGE),
            )
        )

    hass.bus.async_listen("state_changed", follow)
    await install(hass, init_integration)

    begin = panel.messages[0]
    assert begin == {
        "t": "ota_begin",
        "model": "nest-gen2",
        "version": "0.2.0",
        "size": len(PROGRAM),
        "sha512": hashlib.sha512(PROGRAM).hexdigest(),
        "signature": SIGNATURE,
    }
    assert [m["offset"] for m in panel.messages if m["t"] == "ota_chunk"] == [
        0,
        65536,
        131072,
    ]
    assert panel.received() == PROGRAM
    assert [m["t"] for m in panel.messages[-2:]] == ["ota_end", "ota_apply"]

    current = state(hass, init_integration)
    assert current.state == STATE_OFF
    assert current.attributes[ATTR_INSTALLED_VERSION] == "0.2.0"
    assert current.attributes[ATTR_IN_PROGRESS] is False
    # Progress by the pieces sent, and available while the panel restarted.
    assert [p for _, _, p in seen if p is not None] == [43, 85, 100]
    assert all(s != STATE_UNAVAILABLE for s, _, _ in seen)
    assert seen[0][1] is True


@pytest.mark.usefixtures("released", "takes_updates")
async def test_install_rolled_back(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    panel = Panel(hass, clients)
    panel.restart_with = "0.1.0"
    with pytest.raises(HomeAssistantError) as caught:
        await install(hass, init_integration)
    assert caught.value.translation_key == "firmware_rolled_back"
    assert caught.value.translation_placeholders["running"] == "0.1.0"
    assert state(hass, init_integration).attributes[ATTR_IN_PROGRESS] is False


@pytest.mark.usefixtures("released", "takes_updates")
async def test_install_never_comes_back(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    panel = Panel(hass, clients)
    panel.restart_with = None
    with (
        patch.object(update, "FIRMWARE_RESTART_TIMEOUT", 0.05),
        pytest.raises(HomeAssistantError) as caught,
    ):
        await install(hass, init_integration)
    assert caught.value.translation_key == "firmware_no_restart"
    assert state(hass, init_integration).state == STATE_UNAVAILABLE
    await init_integration.runtime_data.async_shutdown()


@pytest.mark.usefixtures("released", "takes_updates")
@pytest.mark.parametrize(
    ("code", "key"),
    [
        ("bad_signature", "firmware_bad_signature"),
        ("downgrade", "firmware_downgrade"),
        ("no_space", "firmware_no_space"),
        ("too_large", "firmware_too_large"),
        ("unsupported", "firmware_unsupported"),
        ("bad_request", "cannot_send"),
    ],
)
async def test_install_refused(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    code: str,
    key: str,
) -> None:
    panel = Panel(hass, clients)
    panel.refuse["ota_begin"] = PanelError(code, "refused")
    with pytest.raises(HomeAssistantError) as caught:
        await install(hass, init_integration)
    assert caught.value.translation_key == key
    assert panel.messages == []
    assert state(hass, init_integration).attributes[ATTR_IN_PROGRESS] is False


@pytest.mark.usefixtures("released", "takes_updates")
async def test_install_loses_the_connection(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    panel = Panel(hass, clients)
    panel.refuse["ota_chunk"] = CannotConnect("the connection is closed")
    with pytest.raises(HomeAssistantError) as caught:
        await install(hass, init_integration)
    assert caught.value.translation_key == "cannot_send"
    assert [m["t"] for m in panel.messages] == ["ota_begin"]


@pytest.mark.usefixtures("takes_updates")
@pytest.mark.parametrize(
    ("program", "key"),
    [
        ({"status": 404}, "firmware_download"),
        ({"content": PROGRAM[:1000]}, "firmware_download"),
        ({"exc": TimeoutError()}, "firmware_download"),
    ],
)
async def test_install_cant_download(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    clients: ClientFactory,
    github: AiohttpClientMocker,
    program: dict[str, Any],
    key: str,
) -> None:
    github.clear_requests()
    github.get(FIRMWARE_RELEASES_URL, json=github_release())
    github.get(SIGNATURE_URL, text=SIGNATURE + "\n")
    github.get(PROGRAM_URL, **program)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    panel = Panel(hass, clients)
    with pytest.raises(HomeAssistantError) as caught:
        await install(hass, config_entry)
    assert caught.value.translation_key == key
    assert panel.messages == []
    await hass.config_entries.async_unload(config_entry.entry_id)


@pytest.mark.usefixtures("takes_updates")
async def test_install_too_large(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    clients: ClientFactory,
    github: AiohttpClientMocker,
) -> None:
    """A program larger than a panel takes isn't downloaded."""
    github.clear_requests()
    github.get(FIRMWARE_RELEASES_URL, json=github_release(size=16 * 1024 * 1024 + 1))
    github.get(SIGNATURE_URL, text=SIGNATURE + "\n")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    panel = Panel(hass, clients)
    with pytest.raises(HomeAssistantError) as caught:
        await install(hass, config_entry)
    assert caught.value.translation_key == "firmware_too_large"
    assert panel.messages == []
    assert all(PROGRAM_URL not in str(call[1]) for call in github.mock_calls)
    await hass.config_entries.async_unload(config_entry.entry_id)
