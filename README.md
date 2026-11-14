# Nesto Panel for Home Assistant

[![Marketplace](https://img.shields.io/badge/Marketplace-Default-41BDF5.svg?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/integrations/marketplace/)
[![Validate](https://github.com/jorgediez/ha-nesto/actions/workflows/validate.yml/badge.svg)](https://github.com/jorgediez/ha-nesto/actions/workflows/validate.yml)
[![Tests](https://github.com/jorgediez/ha-nesto/actions/workflows/tests.yml/badge.svg)](https://github.com/jorgediez/ha-nesto/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

> **Work in progress.** A panel can be added and paired, and stays
> connected; the room's controls, the display, sensors and notifications are
> still to come.

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

Requires Home Assistant 2026.11 or newer, and a panel running the Nesto
firmware.

Until the integration is in the Marketplace's default list, add it from its
link: in *Settings → Marketplace*, choose **Add from link** (it needs a GitHub
connection), enter `https://github.com/jorgediez/ha-nesto`, then **Install**.

Manually: copy `custom_components/nesto` into your Home Assistant's
`custom_components` folder, and restart.

## Configuration

A panel on the same network shows up under *Settings → Devices & services →
Discovered*. Choose **Add**, and the panel shows a six-digit code: type it in
Home Assistant. If discovery doesn't reach your panel (another VLAN, say), add
it with **Add integration → Nesto Panel** and the address and port the
panel shows under *Settings → About*.

A panel pairs with one Home Assistant at a time. A panel already paired with
another one refuses: choose *Settings → Pair* on the panel first, then add it.

- **Pairing again:** if someone chooses *Settings → Forget* on the panel, or
  pairs it with another Home Assistant, the panel stops accepting this one's
  key and Home Assistant asks you to pair again. Follow the notice, and type
  the new code.
- **A new address:** a discovered panel's address is followed by itself. For
  one added by hand, choose **Reconfigure** on its entry; Home Assistant checks
  it's still the same panel.

## Entities

- **Identify** (button, under configuration): the panel wakes, pulses its
  backlight and beeps three times, so you can tell which one it is.

Entities are unavailable while the panel can't be reached; Home Assistant
keeps trying, every minute at most.

## Troubleshooting

To see what passes between Home Assistant and the panel, turn on debug
logging, from the integration's page (**Enable debug logging**) or in
`configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.nesto: debug
```

The integration's diagnostics (*Settings → Devices & services → Nesto Panel
→ ⋮ → Download diagnostics*) say how the panel and its connection stand. The
pairing key, the address and the panel id are left out.

## Removal

Delete the panel's entry in *Settings → Devices & services*. If the panel is
reachable, it forgets Home Assistant's key and waits to be paired again;
otherwise choose *Settings → Forget* on it. Then uninstall the integration from
the Marketplace (**⋮ → Uninstall**), or delete `custom_components/nesto`, and
restart.

## Privacy

Nothing goes over the internet: the panel and Home Assistant talk only on your
network. Home Assistant keeps the panel's address and the pairing key in its
config entry. The key works like a password for the panel: anyone holding it
can act as this Home Assistant towards the panel, so it's left out of the
diagnostics, along with the address and the panel id.

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
