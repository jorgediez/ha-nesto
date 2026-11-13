# Security policy

## Reporting a vulnerability

Please report it privately, through
[Report a vulnerability](https://github.com/jorgediez/ha-nesto/security/advisories/new)
on this repository's Security tab. Don't open a public issue or post it in the
community thread. Only the maintainer sees the report, and no email address is
needed.

Describe what you found, how to reproduce it, and which versions of the
integration, Home Assistant and the panel's firmware you saw it on.

## What counts

- Diagnostics that contain what they are meant to leave out: the pairing key,
  the panel's address or its id (the Wi-Fi MAC address).
- A way to pair with a panel, or to take over its session, without the code
  it shows on screen, or to learn the pairing key by watching the network.
- Anything that lets the panel, or someone on the network pretending to be
  it, act in Home Assistant beyond the entities the user put on the panel and
  the actions those offer.
- A way to make the integration run code, or crash Home Assistant, from what
  a panel sends.

Not a vulnerability in this integration: someone with root access to the
thermostat can read the pairing key from its flash and act as the panel. The
panel only reaches what Home Assistant puts on it, so keep that to what the
room needs.

## Supported versions

Only the latest release gets fixes.

## What to expect

This is a volunteer project. Reports are read and fixed on a best-effort basis,
and you'll hear back in the report itself, where any fix and its release are
coordinated before details are made public.
