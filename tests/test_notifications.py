"""Notifications: the actions, the notify entity and the event (section 14)."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.nesto.client import CannotConnect, PanelError
from custom_components.nesto.const import DOMAIN
from homeassistant.components.event import ATTR_EVENT_TYPE, DOMAIN as EVENT_DOMAIN
from homeassistant.components.notify import (
    ATTR_MESSAGE,
    ATTR_TITLE,
    DOMAIN as NOTIFY_DOMAIN,
    SERVICE_SEND_MESSAGE,
)
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .common import ClientFactory


def entity_id(
    hass: HomeAssistant, entry: MockConfigEntry, domain: str, key: str
) -> str:
    found = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert found is not None, key
    return found


async def notify(
    hass: HomeAssistant, entry: MockConfigEntry, **fields: Any
) -> dict[str, Any]:
    response = await hass.services.async_call(
        DOMAIN,
        "notify",
        {"config_entry_id": entry.entry_id, **fields},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    return dict(response)


def sent(clients: ClientFactory) -> dict[str, Any]:
    return clients.last.request.await_args.args[0]


async def send(hass: HomeAssistant, clients: ClientFactory, message: dict) -> None:
    clients.last.on_message(message)
    await hass.async_block_till_done()


# The notify action -----------------------------------------------------------


async def test_notify_with_everything(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    response = await notify(
        hass,
        init_integration,
        message="Someone is at the door",
        title="Front door",
        id="door-1",
        priority="high",
        duration=30,
        beep="alert",
        icon="mdi:door-open",
        buttons=["Open", "Ignore"],
    )
    assert response == {"id": "door-1"}
    assert sent(clients) == {
        "t": "notify",
        "nid": "door-1",
        "message": "Someone is at the door",
        "title": "Front door",
        "priority": "high",
        "duration_s": 30,
        "beep": "alert",
        "icon": "door",
        "actions": [
            {"id": "open", "label": "Open"},
            {"id": "ignore", "label": "Ignore"},
        ],
    }


async def test_notify_with_the_defaults(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    """Fields left out are left to the panel; an ID is made up."""
    response = await notify(hass, init_integration, message="Washing done")
    request = sent(clients)
    assert request["nid"] == response["id"]
    assert request["nid"].startswith("ha-")
    assert request == {"t": "notify", "nid": request["nid"], "message": "Washing done"}


async def test_an_icon_the_panel_lacks_is_left_out(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    await notify(hass, init_integration, message="Hi", icon="mdi:rocket")
    assert "icon" not in sent(clients)


async def test_a_button_slug(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    await notify(hass, init_integration, message="Hi", buttons="Abrir puerta")
    assert sent(clients)["actions"] == [{"id": "abrir_puerta", "label": "Abrir puerta"}]


@pytest.mark.parametrize(
    ("fields", "key"),
    [
        ({"message": "  "}, "empty_message"),
        ({"message": "x" * 257}, "too_long"),
        ({"message": "Hi", "title": "x" * 65}, "too_long"),
        ({"message": "Hi", "id": "x" * 65}, "too_long"),
        ({"message": "Hi", "id": ""}, "too_long"),
        ({"message": "Hi", "buttons": ["A", "B", "C", "D"]}, "too_many_buttons"),
        ({"message": "Hi", "buttons": ["x" * 65]}, "too_long"),
        ({"message": "Hi", "buttons": ["!!!"]}, "bad_button"),
        ({"message": "Hi", "buttons": ["Open", "open"]}, "bad_button"),
    ],
)
async def test_notify_refused_before_sending(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    fields: dict[str, Any],
    key: str,
) -> None:
    with pytest.raises(ServiceValidationError) as caught:
        await notify(hass, init_integration, **fields)
    assert caught.value.translation_key == key
    clients.last.request.assert_not_awaited()


async def test_the_limits_themselves_are_fine(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    await notify(
        hass,
        init_integration,
        message="x" * 256,
        title="t" * 64,
        id="i" * 64,
        buttons=["A", "B", "C"],
    )
    assert len(sent(clients)["actions"]) == 3


async def test_notify_an_unknown_panel(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    other = MockConfigEntry(domain="demo")
    other.add_to_hass(hass)
    for entry_id in ("nope", other.entry_id):
        with pytest.raises(ServiceValidationError) as caught:
            await hass.services.async_call(
                DOMAIN,
                "notify",
                {"config_entry_id": entry_id, "message": "Hi"},
                blocking=True,
                return_response=True,
            )
        assert caught.value.translation_key == "unknown_panel"


async def test_notify_a_panel_not_set_up(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    await hass.config_entries.async_unload(init_integration.entry_id)
    with pytest.raises(ServiceValidationError) as caught:
        await notify(hass, init_integration, message="Hi")
    assert caught.value.translation_key == "panel_not_loaded"


async def test_the_panel_refuses(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.request_error = PanelError("not_ready", "still connecting")
    with pytest.raises(HomeAssistantError) as caught:
        await notify(hass, init_integration, message="Hi")
    assert caught.value.translation_key == "cannot_send"
    assert "not_ready" in str(caught.value.translation_placeholders)


# Clearing ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"id": "door-1"}, {"t": "notify_clear", "nid": "door-1"}),
        ({}, {"t": "notify_clear", "all": True}),
    ],
)
async def test_clear(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    fields: dict[str, Any],
    message: dict[str, Any],
) -> None:
    await hass.services.async_call(
        DOMAIN,
        "clear_notification",
        {"config_entry_id": init_integration.entry_id, **fields},
        blocking=True,
    )
    assert clients.last.sent[-1] == message


async def test_clear_fails(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    clients.last.send.side_effect = CannotConnect("broken pipe")
    with pytest.raises(HomeAssistantError) as caught:
        await hass.services.async_call(
            DOMAIN,
            "clear_notification",
            {"config_entry_id": init_integration.entry_id},
            blocking=True,
        )
    assert caught.value.translation_key == "cannot_send"


# The notify entity ---------------------------------------------------------------


async def test_send_message(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    await hass.services.async_call(
        NOTIFY_DOMAIN,
        SERVICE_SEND_MESSAGE,
        {
            "entity_id": entity_id(hass, init_integration, NOTIFY_DOMAIN, "notify"),
            ATTR_MESSAGE: "Dinner's ready",
            ATTR_TITLE: "Kitchen",
        },
        blocking=True,
    )
    request = sent(clients)
    assert request["message"] == "Dinner's ready"
    assert request["title"] == "Kitchen"
    assert set(request) == {"t", "nid", "message", "title"}


# The event -------------------------------------------------------------------------


async def test_notification_events(
    hass: HomeAssistant, init_integration: MockConfigEntry, clients: ClientFactory
) -> None:
    event = entity_id(hass, init_integration, EVENT_DOMAIN, "notification")
    shown = {"t": "notify_event", "nid": "door-1", "event": "shown"}
    # Not before the panel is ready (section 8).
    await send(hass, clients, shown)
    assert hass.states.get(event).state == STATE_UNKNOWN

    await send(hass, clients, {"t": "ready"})
    await send(hass, clients, shown)
    state = hass.states.get(event)
    assert state.attributes[ATTR_EVENT_TYPE] == "shown"
    assert state.attributes["id"] == "door-1"
    assert "button" not in state.attributes

    await send(
        hass,
        clients,
        {"t": "notify_event", "nid": "door-1", "event": "action", "action": "open"},
    )
    state = hass.states.get(event)
    assert state.attributes[ATTR_EVENT_TYPE] == "action"
    assert state.attributes["button"] == "open"


@pytest.mark.parametrize(
    "message",
    [
        {"t": "notify_event", "nid": "door-1", "event": "exploded"},
        {"t": "notify_event", "event": "shown"},
        {"t": "notify_event", "nid": "door-1", "event": "action"},
        {"t": "notify_event", "nid": "door-1", "event": "dismissed", "action": "x"},
    ],
)
async def test_a_bad_notification_event_is_ignored(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    clients: ClientFactory,
    message: dict[str, Any],
) -> None:
    event = entity_id(hass, init_integration, EVENT_DOMAIN, "notification")
    await send(hass, clients, {"t": "ready"})
    await send(hass, clients, message)
    assert hass.states.get(event).state == STATE_UNKNOWN
