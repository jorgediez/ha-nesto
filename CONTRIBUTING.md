# Contributing

Thanks for looking. The integration is young, and reports from real panels
are what it needs most.

## Reporting a panel, or a problem

Bugs are best as an [issue](https://github.com/jorgediez/ha-nesto/issues/new/choose),
where there is a form for bugs, one for telling us how the integration does on
your panel, and one for ideas. (A community thread for questions will follow
the first release.)

What makes a report actionable:

- The integration's diagnostics (*Settings → Devices & services → Nesto Panel
  → ⋮ → Download diagnostics*), once they exist. The pairing key, the
  address and the panel id are redacted.
- A debug log covering the moment it went wrong, with
  `custom_components.nesto: debug` in your `logger` settings.
- For a panel that won't pair or connect, the output of
  [`scripts/nesto_cli.py`](scripts/nesto_cli.py), which talks to a panel
  directly, without Home Assistant: `python scripts/nesto_cli.py HOST info`.

A security problem should not go in an issue: see [SECURITY.md](SECURITY.md)
for how to report it privately.

## What belongs here

This integration is Home Assistant's side of the
[Nesto Panel protocol](https://github.com/jorgediez/nesto-firmware/blob/main/PROTOCOL.md).

Changes that fit:

- Mapping more of Home Assistant onto the panel's items.
- Exposing more of what the panel reports or accepts.
- Fixing how the integration reacts to what a panel sends.
- Documentation.

Changes that belong elsewhere:

- The panel's screens, sounds and hardware: the firmware repository.
- A change to the protocol itself: the firmware repository first, where
  PROTOCOL.md and the test vectors live.

**If a change is more than a small fix, open an issue first.** A short
description of what you want and how you'd do it takes minutes and can save
you days.

## Working on the code

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest --cov
```

The Home Assistant test harness only runs on Linux and macOS. With Docker you
can run the suite on any OS, Windows included, with the same matrix as CI:

```bash
scripts/test-docker.sh                 # both jobs
scripts/test-docker.sh min -k pairing  # one job ("min" or "latest"), extra pytest args
scripts/test-docker.sh --cov           # with a coverage report
REBUILD=1 scripts/test-docker.sh       # rebuild the images anyway; a new pin rebuilds by itself
```

`tests/vectors/protocol-v1.json` is a copy of the firmware's test vectors, so
both sides check against the same bytes. Copy it again when the firmware's
changes; never edit it here.

A pull request is expected to:

- Pass `ruff check`, `ruff format --check` and the test suite on both Home
  Assistant versions. CI runs these, plus HACS and `hassfest`.
- Come with tests. The client is tested against a pretend panel
  (`tests/fake_panel.py`); the integration, inside Home Assistant.
- Update the docs it affects.
- Explain, in the commit message, why the change is the way it is.

Keep a pull request to one subject. Two unrelated improvements are two pull
requests, and they'll both move faster.

## Style

Follow [Home Assistant's own conventions](https://developers.home-assistant.io/docs/development_guidelines)
and the code already here: entity names and errors translated, nothing
blocking in the event loop, comments that say why rather than what, and no
Home Assistant imports in `protocol.py` and `client.py`, so the scripts can
use them. Ruff, at line length 88, settles formatting.

## Releasing

1. Bump `version` in `custom_components/nesto/manifest.json` and in
   `pyproject.toml`. A test checks they match.
2. Publish a GitHub release tagged `vX.Y.Z`, with notes for users.
3. The **Release** workflow checks the tag matches both files, then runs the
   tests and hassfest against the tagged code, and the HACS validation, which
   checks the repository through GitHub's API rather than the tag.

## License

Contributions are under the [Apache License 2.0](LICENSE), like the rest of
the project.
