"""Checks on the files that describe the integration rather than run it."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tomllib

import pytest

from homeassistant.const import MAJOR_VERSION, MINOR_VERSION

REPO = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(
    (REPO / "custom_components" / "nesto" / "manifest.json").read_text("utf-8")
)


def test_the_version_is_declared_once():
    """manifest.json and pyproject.toml carry the same version.

    The Marketplace shows the manifest's; a mismatch here would otherwise
    surface only when a release is tagged.
    """
    project = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))
    assert MANIFEST["version"] == project["project"]["version"]


def test_requirements_are_tested_at_their_pins():
    """Each manifest requirement is in requirements_test.txt at the same pin.

    There are none today: cryptography comes with Home Assistant core, so the
    manifest doesn't list it and core's own pin is what runs.
    """
    tested = {
        line.strip()
        for line in (REPO / "requirements_test.txt").read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for requirement in MANIFEST["requirements"]:
        assert re.fullmatch(r"[\w.-]+==[\w.]+", requirement), requirement
        assert requirement in tested


def _minimum_home_assistant() -> tuple[int, int]:
    """Return the oldest supported release from hacs.json, as (year, month)."""
    hacs = json.loads((REPO / "hacs.json").read_text(encoding="utf-8"))
    year, month, *_ = hacs["homeassistant"].split(".")
    return int(year), int(month)


def test_runs_on_a_supported_home_assistant():
    """A release older than hacs.json's minimum means a stale pin somewhere."""
    running = (MAJOR_VERSION, MINOR_VERSION)
    assert running >= _minimum_home_assistant()


@pytest.mark.skipif(
    os.environ.get("HA_MIN") != "true", reason="only in the min job, which sets it"
)
def test_the_min_job_runs_the_minimum_from_hacs_json():
    """The min job's harness pin lives in the workflow, apart from hacs.json.

    Raising or lowering one without the other would test a release nobody
    promised; this catches it in the job itself.
    """
    running = (MAJOR_VERSION, MINOR_VERSION)
    assert running == _minimum_home_assistant()
