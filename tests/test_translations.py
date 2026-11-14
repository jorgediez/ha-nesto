"""Checks on the translations."""

from __future__ import annotations

import json
from pathlib import Path
import re

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components" / "nesto"
EN = json.loads((INTEGRATION / "translations" / "en.json").read_text("utf-8"))
# Home Assistant raises these itself, without the integration naming them.
BUILTIN_ABORTS = {
    "already_configured",  # _abort_if_unique_id_configured
    "reauth_successful",
    "reconfigure_successful",
}


def _sources() -> str:
    return "\n".join(p.read_text("utf-8") for p in INTEGRATION.glob("*.py"))


def test_every_raised_error_has_a_message():
    """A missing message shows the bare translation key to the user."""
    raised = set(
        re.findall(r'translation_domain=DOMAIN,\s*translation_key="(\w+)"', _sources())
    )
    assert raised, "the scan found no translated errors"
    assert raised == set(EN["exceptions"])


def test_every_issue_is_raised():
    raised = set(re.findall(r'translation_key="(\w+)"', _sources()))
    assert set(EN["issues"]) <= raised


def test_every_flow_error_and_abort_is_used():
    """A message no code raises is dead weight, and likely a typo."""
    sources = _sources()
    for key in [*EN["config"]["error"], *EN["config"]["abort"]]:
        assert key in BUILTIN_ABORTS or f'"{key}"' in sources, key


def test_every_flow_step_exists():
    sources = _sources()
    for step in EN["config"]["step"]:
        assert f"async_step_{step}" in sources, step
