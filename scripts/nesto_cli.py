"""Talk to a Nesto panel from the command line, without Home Assistant.

Uses the integration's own client, so it checks the same code against a real
panel or the firmware's simulator:

    python scripts/nesto_cli.py HOST pair      # type the code the panel shows
    python scripts/nesto_cli.py HOST info      # the panel's info request
    python scripts/nesto_cli.py HOST identify  # wake it, pulse and beep
    python scripts/nesto_cli.py HOST watch     # synchronise, print what it sends

`pair` saves the key to a file (nesto-<panel id>.key in the current folder,
or --key), which the other commands read. Needs `cryptography`.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

# The repository root, so the integration's client imports without Home Assistant.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.nesto.client import (
    DEFAULT_PORT,
    NestoClient,
    NestoError,
)


def key_file(args: argparse.Namespace, panel_id: str) -> Path:
    return Path(args.key or f"nesto-{panel_id}.key")


async def pair(args: argparse.Namespace) -> int:
    client = NestoClient(args.host, args.port)
    hello = await client.connect()
    print(f"Panel {hello.panel_id}, firmware {hello.fw}")
    code = await client.begin_pairing()
    typed = (
        args.code
        if args.code
        else await asyncio.to_thread(input, "Code shown on the panel: ")
    )
    if typed.strip() != code:
        await client.cancel_pairing()
        print(
            f"The code doesn't match ({typed.strip()!r}, expected {code}); pairing cancelled."
        )
        return 1
    key = await client.confirm_pairing()
    path = key_file(args, hello.panel_id)
    path.write_bytes(key)
    print(f"Paired; key saved to {path}")
    return 0


async def session(args: argparse.Namespace, **kwargs) -> NestoClient:
    client = NestoClient(args.host, args.port, **kwargs)
    hello = await client.connect()
    await client.authenticate(key_file(args, hello.panel_id).read_bytes())
    return client


async def info(args: argparse.Namespace) -> int:
    client = await session(args)
    result = await client.request({"t": "info"})
    print(json.dumps({k: v for k, v in result.items() if k not in ("t", "re")}))
    await client.close()
    return 0


async def identify(args: argparse.Namespace) -> int:
    client = await session(args)
    await client.request({"t": "identify"})
    print("Identifying")
    await client.close()
    return 0


async def watch(args: argparse.Namespace) -> int:
    closed: asyncio.Future[Exception | None] = (
        asyncio.get_running_loop().create_future()
    )

    def show(message: dict) -> None:
        print(json.dumps(message, ensure_ascii=False))

    client = await session(args, on_message=show, on_close=closed.set_result)
    now = datetime.now().astimezone()
    offset = now.utcoffset() or timedelta()
    await client.send(
        {
            "t": "time",
            "utc_ms": int(now.timestamp() * 1000),
            "utc_offset_min": int(offset.total_seconds() // 60),
        }
    )
    await client.send({"t": "config"})
    await client.send({"t": "layout", "rev": 1, "title": "nesto_cli", "items": []})
    await client.send({"t": "states", "full": True, "items": {}})
    try:
        if args.seconds:
            async with asyncio.timeout(args.seconds):
                reason = await closed
        else:
            reason = await closed
    except TimeoutError:
        await client.close()
        return 0
    print(f"Connection closed: {reason}")
    return 1


COMMANDS = {"pair": pair, "info": info, "identify": identify, "watch": watch}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--key", help="the key file (default nesto-<panel id>.key)")
    parser.add_argument("--code", help="pair: the code shown, instead of asking")
    parser.add_argument("--seconds", type=float, help="watch: stop after this long")
    args = parser.parse_args()
    try:
        return asyncio.run(COMMANDS[args.command](args))
    except (NestoError, OSError) as err:
        print(f"{type(err).__name__}: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
