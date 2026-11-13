# Nesto Panel for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Validate](https://github.com/jorgediez/ha-nesto/actions/workflows/validate.yml/badge.svg)](https://github.com/jorgediez/ha-nesto/actions/workflows/validate.yml)
[![Tests](https://github.com/jorgediez/ha-nesto/actions/workflows/tests.yml/badge.svg)](https://github.com/jorgediez/ha-nesto/actions/workflows/tests.yml)
[![License](https://img.shields.io/github/license/jorgediez/ha-nesto)](LICENSE)

> **Work in progress.** Nothing here can be installed yet: this is the
> protocol and its client, with pairing and the entities to come.

Turns a Nest Learning Thermostat (2nd generation), running the
[Nesto firmware](https://github.com/jorgediez/nesto-firmware), into a room
panel for Home Assistant. Turn the ring and press to control the lights,
climate, covers, switches and scenes of a room; the panel also shows Home
Assistant's notifications, a clock while it rests, and reports its own
temperature, humidity, light and motion sensors.

Everything is local. The panel announces itself on the network, pairs once
with a code it shows on screen, and from then on keeps one encrypted
connection with Home Assistant, which pushes what the panel shows and carries
out what the user does on it. The panel holds no Home Assistant token and
never talks to the internet.

Tested with: the simulator from nesto-firmware. Real thermostats follow.

## Features

Planned for the first release:

- Discovery, and pairing with the code the panel shows.
- Room mode (an area, minus anything you exclude) or manual mode (the
  entities you pick).
- The panel's display as a light, its standby screen as a select, and its
  settings as configuration entities.
- Its temperature, humidity, light and motion sensors.
- Presses (single, double, long) and notification buttons as events.
- Notifications, with priorities, beeps and up to three buttons.

## Installation

Not yet installable.

## Writing to the panel

Home Assistant writes to the panel only what the panel exists to show: when a
connection opens it sends the time, the panel's settings, the room's layout
and the entities' states, and afterwards whatever changes. The panel keeps no
settings of its own to overwrite: its menu only pairs, forgets Home Assistant
and shows information, and the settings Home Assistant sends first are the
firmware's defaults. Anything else, such as a notification, is sent only when
you call an action or change an entity.

## Disclaimer

Unofficial. Not affiliated with, endorsed by or connected to Google or Nest.
"Nest" and "Nest Learning Thermostat" are trademarks of Google LLC, used here
only to say which device the firmware runs on. Replacing a thermostat's
software is at your own risk.

## License

[Apache License 2.0](LICENSE). The protocol module is ported from
nesto-firmware's reference implementation, by the same author.
