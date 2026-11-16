"""Building the panel's notifications (PROTOCOL.md section 14).

Checked here against the protocol's limits, so a caller gets a clear error
before anything is sent, rather than the panel's `bad_request`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import slugify, ulid as ulid_util

from .const import DOMAIN
from .room import builtin_icon

MAX_MESSAGE: Final = 256
MAX_TITLE: Final = 64
MAX_NID: Final = 64
MAX_LABEL: Final = 64
MAX_ACTIONS: Final = 3
MAX_DURATION: Final = 86400  # the firmware takes up to a day

PRIORITIES: Final = ("low", "normal", "high", "critical")
BEEPS: Final = ("none", "short", "alert")
# What the panel reports about a notification.
EVENTS: Final = ("shown", "dismissed", "expired", "action")


def _too_long(field: str, limit: int) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="too_long",
        translation_placeholders={"field": field, "limit": str(limit)},
    )


def new_nid() -> str:
    """Return a notification ID for a caller that didn't give one."""
    return f"ha-{ulid_util.ulid_now().lower()}"


def build_notification(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return a `notify` request, or raise if it breaks the panel's limits.

    `fields` are the `nesto.notify` action's: `message`, and optionally
    `title`, `id`, `priority`, `duration`, `beep`, `icon` and `buttons`. Each
    button's ID is its label as a slug, so "Open" sends `open` back. Fields
    left out take the panel's defaults.
    """
    message: str = fields["message"]
    title: str | None = fields.get("title")
    nid: str | None = fields.get("id")
    if not message.strip():
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="empty_message"
        )
    if len(message) > MAX_MESSAGE:
        raise _too_long("message", MAX_MESSAGE)
    if title is not None and len(title) > MAX_TITLE:
        raise _too_long("title", MAX_TITLE)
    if nid is not None and not 1 <= len(nid) <= MAX_NID:
        raise _too_long("id", MAX_NID)
    request: dict[str, Any] = {
        "t": "notify",
        "nid": nid or new_nid(),
        "message": message,
    }
    if title:
        request["title"] = title
    for field, key in (
        ("priority", "priority"),
        ("duration", "duration_s"),
        ("beep", "beep"),
    ):
        if fields.get(field) is not None:
            request[key] = fields[field]
    if builtin := builtin_icon(fields.get("icon")):
        request["icon"] = builtin
    if buttons := fields.get("buttons"):
        request["actions"] = _actions(buttons)
    return request


def _actions(buttons: list[str]) -> list[dict[str, str]]:
    if len(buttons) > MAX_ACTIONS:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="too_many_buttons",
            translation_placeholders={"limit": str(MAX_ACTIONS)},
        )
    actions: list[dict[str, str]] = []
    for label in buttons:
        if len(label) > MAX_LABEL:
            raise _too_long("button", MAX_LABEL)
        # slugify gives "unknown" for a label without letters or digits.
        action_id = slugify(label)
        if not any(c.isalnum() for c in label) or any(
            a["id"] == action_id for a in actions
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="bad_button",
                translation_placeholders={"label": label},
            )
        actions.append({"id": action_id, "label": label})
    return actions
