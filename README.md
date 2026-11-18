# Nesto Panel for Home Assistant

[![Marketplace](https://img.shields.io/badge/Marketplace-Custom-41BDF5.svg?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/integrations/marketplace/)
[![Validate](https://github.com/jorgediez/ha-nesto/actions/workflows/validate.yml/badge.svg)](https://github.com/jorgediez/ha-nesto/actions/workflows/validate.yml)
[![Tests](https://github.com/jorgediez/ha-nesto/actions/workflows/tests.yml/badge.svg)](https://github.com/jorgediez/ha-nesto/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Turns a Nest Learning Thermostat (2nd generation), running the
[Nesto firmware](https://github.com/jorgediez/nesto-firmware), into a room
panel for Home Assistant. Turn the ring and press to control the lights,
climate, covers, switches and scenes of a room; the panel also shows Home
Assistant's notifications, a clock while it rests, and reports its own
temperature, humidity, light and motion sensors.

<p align="center">
  <img src="https://raw.githubusercontent.com/jorgediez/nesto-firmware/v0.1.0/docs/images/home.png" width="160" alt="The panel's home screen, showing a light">
  <img src="https://raw.githubusercontent.com/jorgediez/nesto-firmware/v0.1.0/docs/images/climate.png" width="160" alt="A radiator's target temperature">
  <img src="https://raw.githubusercontent.com/jorgediez/nesto-firmware/v0.1.0/docs/images/notification.png" width="160" alt="A notification with buttons">
  <img src="https://raw.githubusercontent.com/jorgediez/nesto-firmware/v0.1.0/docs/images/clock-analog.png" width="160" alt="The standby clock">
</p>

Everything is local. The panel announces itself on the network, pairs once
with a code it shows on screen, and from then on keeps one encrypted
connection with Home Assistant, which pushes what the panel shows and carries
out what the user does on it. The panel holds no Home Assistant token and
never talks to the internet.

Tested on a Nest Learning Thermostat (2nd generation) with firmware 0.1.0,
on its EU base and a USB stand, and against the firmware's simulator.

## Features

- Discovery, and pairing with the code the panel shows.
- Room mode (the panel's area, following it as it changes) or manual mode
  (the entities you pick), either way in the order you choose.
- The panel's display as a light, its standby screen as a select, and its
  settings as configuration entities.
- Its temperature, humidity, light and motion sensors.
- Presses (single, double, long) and notification buttons as events.
- Notifications, with priorities, beeps and up to three buttons.

## Installation

Requires Home Assistant 2026.11 or newer, and a panel running the Nesto
firmware, 0.1.0 or newer: download a
[release](https://github.com/jorgediez/nesto-firmware/releases) and follow its
[install guide](https://github.com/jorgediez/nesto-firmware/blob/main/docs/INSTALL.md).

### Marketplace (recommended)

[![Open your Home Assistant instance and open a repository inside the Marketplace.][my-marketplace-badge]][my-marketplace-url]

1. Use the button above. Until the integration is in the Marketplace's
   default list, you can also add it from its link: in *Settings →
   Marketplace*, choose **Add from link** (it needs a GitHub connection) and
   enter `https://github.com/jorgediez/ha-nesto`.
2. Select **Install**. If a restart is needed, Home Assistant offers one.

### Manual

Copy `custom_components/nesto` into your Home Assistant's `custom_components`
folder, and restart.

## Configuration

A panel on the same network shows up under *Settings → Devices & services →
Discovered*. Choose **Add**, and the panel shows a six-digit code: type it in
Home Assistant.

If discovery doesn't reach your panel (another VLAN, say), add it by address:

[![Open your Home Assistant instance and start setting up Nesto Panel.][my-setup-badge]][my-setup-url]

Or *Settings → Devices & services → Add integration → Nesto Panel*. Enter the
address and port the panel shows under *Settings → Technical info* (Network,
and Nesto's Port), then the code.

A panel pairs with one Home Assistant at a time. A panel already paired with
another one refuses: choose *Settings → Pair* on the panel first, then add it.

- **Pairing again:** if someone chooses *Settings → Forget* on the panel, or
  pairs it with another Home Assistant, the panel stops accepting this one's
  key and Home Assistant asks you to pair again. Follow the notice, and type
  the new code.
- **A new address:** a discovered panel's address is followed by itself. For
  one added by hand, choose **Reconfigure** on its entry; Home Assistant checks
  it's still the same panel.

## Choosing the room

The panel shows the area its device is in. Home Assistant asks for an area
when you add the panel; to change it, open the panel's device page and set
its area. Until it's in an area, the panel shows only its name.

In the area, the panel shows the lights, switches, input booleans, fans
without speed control, scenes, scripts, climate entities and covers: those
put in the area, and those whose device is in it, unless the entity was put
in another area. Hidden entities, and configuration and diagnostic ones, are
left out. The area's name is the panel's title, and dropped from the front
of the entities' names ("Living room lamp" shows as "Lamp").

The integration's **Configure** options choose between:

- **The panel's room:** the area's entities, which you can reorder, take off,
  or add to.
- **Entities I pick:** only the entities you choose, whatever their area,
  under the entry's name.

Either way, the next form has a section per kind: lights, climate, covers,
switches (with input booleans and fans), scenes (with scripts) and sensors.
The panel shows them in that order, and within each section in the order
you drag them into. In the panel's room, the sections start with the area's
entities, by name, except sensors, which a room doesn't show unless you add
them; with entities you pick, they start empty.

The panel's room keeps following the area: an entity added to it later
appears after the ones in your list, by name, and one you took off stays
off. If the panel moves to another area, it shows that area's entities until
you save the options again; the sensors you added stay.

The Lights section can add an **All lights** item, first on the panel, which
turns all of them on and off together and dims them. It's off unless you
tick it, and needs two or more lights.

The panel shows 48 items at most; the log says which were left out. Saving
the options reconnects the panel, which takes a second.

What each kind can do on the panel:

| Kind | From | On the panel |
|---|---|---|
| Light | `light` | On, off, brightness, colour temperature, colour and effects, as the light supports |
| All lights | every light shown | On, off and brightness for all of them at once |
| Switch | `switch`, `input_boolean`, `fan` without speeds | On and off |
| Scene | `scene`, `script` | Activate (a script starts; the panel doesn't wait for it to finish) |
| Climate | `climate` | Target temperature or range, HVAC mode, preset and fan mode, as the entity supports |
| Cover | `cover` | Open, close, stop, position and tilt, as the cover supports |
| Sensor | `sensor` | Its value, with the precision Home Assistant shows; read only |

When something on the panel can't be done (the entity is unavailable, or its
action fails in Home Assistant), the panel says so briefly and shows the
entity's last known state again.

## Entities

- **Display** (light): on while the screen shows the controls or the standby
  clock, off when the screen is off. Its brightness is what the screen shows
  now, which is lower in standby, and in a dark room with auto brightness.
  Turning it on wakes the screen; turning it on with a brightness also changes
  the *Brightness* setting. Turning it off switches the screen off until you
  touch the panel (unless *Wake from off by touch* is off) or turn it on again.
- **Temperature**, **Humidity** (sensors): the base's readings, every 30
  seconds. Temperature includes the *Temperature offset* setting.
- **Light level** (sensor): the light sensor's raw value, higher brighter. It
  isn't in lux: about 100 in a dark room, 2000 in a lit one.
- **Motion** (binary sensor): someone moving in the room; off 30 seconds after
  the last movement.
- **Proximity** (binary sensor): a hand near the glass; off 5 seconds after
  it's gone.
- **Press** (event): a single, double or long press. The event's `context`
  says where: `ui` (on the controls, which it also worked), `standby` or
  `off`. A double press only comes in standby or off, and doesn't wake the
  panel, so it can trigger anything while the panel stays asleep.
- **Notifications** (notify): `notify.send_message` shows a message on the
  panel (see *Notifications*).
- **Notification** (event): what happened to a notification (see
  *Notifications*).
- **Identify** (button, under configuration): the panel wakes, pulses its
  backlight and beeps three times, so you can tell which one it is.
- Under diagnostic: **Battery voltage**, **Last restart** and **Base
  connected**; and, disabled until you enable them, **Supply voltage**, **USB
  voltage**, **Free memory**, **Wi-Fi signal** (dBm), **IP address** and
  **Base firmware**. Home Assistant asks the panel for these every five
  minutes.

When it connects, Home Assistant also tells the panel its own version and
this integration's, which the panel shows in Settings → Technical info.

The sensors exist only for a panel on its base: a head unit off its base has
none, and when it's put back, its entry reloads and they appear. Everything
except the settings is unavailable while the panel can't be reached; Home
Assistant keeps trying, every minute at most.

To turn the room's lights off with a double press while the panel sleeps
(the trigger fires on every press; the condition picks the double ones):

```yaml
triggers:
  - trigger: state
    entity_id: event.nesto_panel_0a1b2c_press
    not_to: unavailable
conditions:
  - condition: state
    entity_id: event.nesto_panel_0a1b2c_press
    attribute: event_type
    state: double_press
actions:
  - action: light.turn_off
    target:
      area_id: living_room
```

### Settings

These are under configuration on the panel's device page. Home Assistant keeps
them and sends them to the panel whenever one changes, and each time the panel
connects. They stay available while the panel is away; a change made then
reaches it when it's back. They start at the firmware's defaults (shown in
brackets), so a panel nobody has configured behaves as it always has.

| Setting | What it does |
|---|---|
| Brightness | The screen's brightness while showing the controls, 1–100% (80%). |
| Standby brightness | The brightness of the standby clock, 0–100% (20%). |
| Screen timeout | Seconds without a touch before the screen goes to standby, or off when *Standby screen* is *Screen off*; 0 means never (30). |
| Auto brightness | Dims both brightnesses in a dark room, down to a quarter, using the panel's light sensor (on). |
| Wake on motion | A hand near the glass wakes the panel from standby (on). |
| Wake from off by touch | Turning the ring or pressing wakes the screen from off. When this is off, only Home Assistant can turn it back on (on). |
| Standby screen | *Clock*, or *Screen off* instead of standby (Clock). |
| Clock style | *Analog* or *Digital* (Analog). |
| Clock seconds | The clock shows seconds (off). |
| Clock date | The clock shows the date (on). |
| 12-hour clock | A 12-hour clock instead of 24 (off). |
| Ring click | A click on each step of the ring (on). |
| Press click | A click on pressing the screen (off). |
| Notification sounds | Notifications may beep (on). |
| Temperature offset | Added to the temperature reading, −10 to +10 °C (0). The head unit warms itself, so its reading runs high: set this to the difference from a thermometer next to it. Home Assistant's own, never sent to the panel; only on a panel with a temperature sensor. |

## Notifications

`notify.send_message` on the panel's *Notifications* entity shows a normal
notification: it pops up for 15 seconds with a short beep, then waits in the
list on the panel's home screen.

The `nesto.notify` action does more:

| Field | |
|---|---|
| `config_entry_id` | The panel. |
| `message` | Required, up to 256 characters. |
| `title` | Up to 64 characters. |
| `id` | Sending again with the same ID replaces that notification. Home Assistant makes one up if you leave it out, and returns it. |
| `priority` | `low` (a badge on the home screen, no pop-up), `normal` (pops up, wakes the panel from standby), `high` (also wakes it when the screen is off) or `critical` (stays until answered, and beeps every 30 seconds). Default `normal`. |
| `duration` | Seconds on screen before it goes to the list; 0 means until dismissed. Default 15. |
| `beep` | `none`, `short` or `alert`. Default `short`. Silent when *Notification sounds* is off, except for critical ones. |
| `icon` | Shown as the closest of the panel's built-in icons (a door, a bell, a light…), or left out. |
| `buttons` | Up to 3 labels. The ring chooses among them and *Dismiss*, and a press answers. A button's ID is its label in lower case, with `_` for spaces: *Open* is `open`. |

`nesto.clear_notification` removes one notification by its `id`, or all of
them.

The *Notification* event entity says what happened to each one: `shown`,
`dismissed`, `expired` (its time on screen ran out unanswered; it stays in the
list) or `action`. Each event has the notification's `id`, and an `action`
also has the `button`. For example, a doorbell that offers to light the porch:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.doorbell
    to: "on"
actions:
  - action: nesto.notify
    data:
      config_entry_id: 01JXXXXXXXXXXXXXXXXXXXXXXX  # the panel's entry
      id: doorbell
      title: Front door
      message: Someone is at the door. Light the porch?
      priority: high
      beep: alert
      icon: mdi:door
      buttons: [Light, Ignore]
  - wait_for_trigger:
      - trigger: state
        entity_id: event.nesto_panel_0a1b2c_notification
    timeout: "00:01:00"
  - if:
      - condition: template
        value_template: >-
          {{ wait.trigger is not none
             and wait.trigger.to_state.attributes.id == 'doorbell'
             and wait.trigger.to_state.attributes.button == 'light' }}
    then:
      - action: light.turn_on
        target:
          entity_id: light.porch
```

The panel keeps the 10 most recent notifications in memory: a panel that
restarts has none, and Home Assistant doesn't send them again.

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
→ ⋮ → Download diagnostics*) say how the panel and its connection stand, how
many items of each kind it shows, the display's state, the settings, the
panel's status, and which sensors reported. The pairing key, the address, the panel
id, and which entities and areas it shows are left out.

- **The panel shows only its name:** it isn't in an area yet (see *Choosing
  the room*), or the area has nothing the panel can show.
- **No temperature, humidity or motion:** the head unit isn't on its base, or
  the base doesn't answer (*Base connected* is off).
- **The screen stays off:** something turned the *Display* light off, and
  *Wake from off by touch* is off. Turn the light on, or that setting.
- **An entity is missing:** check it's in the panel's area (or its device is),
  that it isn't hidden, and that it's of a kind the panel shows. Sensors only
  appear when picked in the options.

## Supported devices

The Nest Learning Thermostat (2nd generation), display model Display-2.x,
running the [Nesto firmware](https://github.com/jorgediez/nesto-firmware) 0.1.0 or newer, on its base or on a USB
stand. Off its base it has no sensors. Other Nest models, and thermostats
with the stock Nest software, aren't supported: Home Assistant's own Google
Nest integration is for those.

## How data updates

The panel and Home Assistant keep one connection open. Home Assistant sends
the panel each change of the entities it shows as it happens; the panel
sends its sensor readings every 30 seconds, sooner when motion or someone
near changes, and presses and notification answers as they happen. The
diagnostic sensors (battery, memory, Wi-Fi signal and the like) are asked
for every five minutes. A dropped connection is retried, every minute at
most, and the panel gets everything again when it's back.

## Use cases

- A light switch with a dial by the door: the room's lights, dimmed with the
  ring, and the heating's target temperature.
- A bedside panel: a clock while it rests, a scene to turn everything off, and
  a double press, which doesn't wake it, for an automation.
- A doorbell or a leak alarm that you can answer from the wall, with buttons.
- A room's temperature, humidity and motion for automations, from a
  thermostat that would otherwise be thrown away.

## Known limitations

- One Home Assistant per panel, and one panel per entry.
- The panel shows 48 items at most, and only the kinds in *Choosing the
  room*: no media players, locks or fans with speeds yet.
- Notifications live in the panel's memory: a restart loses them, and Home
  Assistant doesn't send them again.
- Not yet: a picture carousel while the panel rests, languages other than
  English on the panel, rebooting it from Home Assistant, and updating its
  firmware from Home Assistant (it's installed over SSH).
- The light level is the sensor's raw value, not lux.
- Tested on one thermostat; running on a wall's heating wires, rather than a
  USB stand, is untested on the firmware's side.

## Removal

Delete the panel's entry in *Settings → Devices & services*. If the panel is
reachable, it forgets Home Assistant's key and waits to be paired again;
otherwise choose *Settings → Forget* on it. Then uninstall the integration from
the Marketplace (**⋮ → Uninstall**), or delete `custom_components/nesto`, and
restart.

## Privacy

Nothing goes over the internet: the panel and Home Assistant talk only on your
network. Anyone at the panel can control what it shows, as with a light
switch on the wall: take off its room anything that shouldn't be one
press away, such as a lock or an alarm script. Home Assistant keeps the
panel's address and the pairing key in its config entry, and the panel's
settings in its storage, deleted when you remove the panel. The key works
like a password for the panel: anyone holding it can act as this Home
Assistant towards the panel, so it's left out of the diagnostics, along with
the address and the panel id.

## Writing to the panel

Home Assistant writes to the panel only what the panel exists to show: when a
connection opens it sends the time, the panel's settings, the room's layout
and the entities' states, and afterwards whatever changes. The panel keeps no
settings of its own to overwrite: its menu only pairs, forgets Home Assistant,
joins Wi-Fi networks and shows information, and the settings Home Assistant
sends are the firmware's defaults until you change one. Anything else, such as a notification, is sent only when
you call an action or change an entity.

## Disclaimer

Unofficial. Not affiliated with, endorsed by or connected to Google or Nest.
"Nest" and "Nest Learning Thermostat" are trademarks of Google LLC, used here
only to say which device the firmware runs on. Replacing a thermostat's
software is at your own risk.

## License

[Apache License 2.0](LICENSE). The protocol module is ported from
nesto-firmware's reference implementation, by the same author.

[my-marketplace-badge]: https://my.home-assistant.io/badges/marketplace_repository.svg
[my-marketplace-url]: https://my.home-assistant.io/redirect/marketplace_repository/?owner=jorgediez&repository=ha-nesto&category=integration
[my-setup-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
[my-setup-url]: https://my.home-assistant.io/redirect/config_flow_start/?domain=nesto
