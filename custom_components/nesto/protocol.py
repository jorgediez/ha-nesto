"""The Nesto Panel protocol's framing and cryptography.

PROTOCOL.md sections 3, 6 and 7 in the firmware repository
(github.com/jorgediez/nesto-firmware), ported from its reference
implementation, tools/nesto_protocol.py. The panel's tests and these check
against the same test vectors (tests/vectors/protocol-v1.json), so both sides
agree byte for byte.

cryptography comes with Home Assistant core, which pins it, so the manifest
doesn't list it again. No Home Assistant imports: the client and the scripts
use it as is.
"""

from __future__ import annotations

import hashlib
import hmac
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAX_FRAME = 512 * 1024
HEADER_SIZE = 4
TAG_SIZE = 16
KEY_SIZE = 32
NONCE_SIZE = 16

ROLE_HA = "ha"
ROLE_PANEL = "panel"


class ProtocolError(Exception):
    """A frame or handshake value is invalid."""


def hkdf_sha512(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """Derive a key with HKDF-SHA-512."""
    return HKDF(hashes.SHA512(), length, salt, info).derive(ikm)


def hmac_sha512(key: bytes, message: bytes) -> bytes:
    """Return HMAC-SHA-512 of a message."""
    return hmac.new(key, message, hashlib.sha512).digest()


# Pairing, section 6.2 -------------------------------------------------------


def public_key(private: bytes) -> bytes:
    """Return the X25519 public key of a private key."""
    return X25519PrivateKey.from_private_bytes(private).public_key().public_bytes_raw()


def shared_secret(private: bytes, peer_public: bytes) -> bytes:
    """Return the X25519 shared secret, rejecting the all-zero one.

    A peer that sends a low-order point forces an all-zero secret, which would
    make the pairing key predictable.
    """
    try:
        peer = X25519PublicKey.from_public_bytes(peer_public)
        secret = X25519PrivateKey.from_private_bytes(private).exchange(peer)
    except ValueError as err:
        # cryptography refuses an all-zero result itself, with ValueError.
        raise ProtocolError(f"X25519 failed: {err}") from err
    if secret == bytes(KEY_SIZE):
        raise ProtocolError("X25519 produced an all-zero shared secret")
    return secret


def pairing_code(ha_pub: bytes, panel_pub: bytes, shared: bytes) -> str:
    """Return the six digits the panel shows and the user types in."""
    digest = hashlib.sha512(b"nesto-sas-v1" + ha_pub + panel_pub + shared).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"


def pairing_key(shared: bytes, ha_pub: bytes, panel_pub: bytes) -> bytes:
    """Return the 32-byte key the panel and Home Assistant keep after pairing."""
    return hkdf_sha512(shared, ha_pub + panel_pub, b"nesto-pair-key-v1")


def confirm_mac(key: bytes, role: str) -> bytes:
    """Return the MAC one side sends to prove it derived the same key."""
    return hmac_sha512(key, f"nesto-confirm-{role}-v1".encode())


# Authentication and session keys, sections 7.1 and 7.2 ---------------------


def auth_mac(
    key: bytes, role: str, panel_nonce: bytes, ha_nonce: bytes, panel_id: str
) -> bytes:
    """Return one side's authentication MAC.

    Home Assistant's covers the panel's nonce first; the panel's, Home
    Assistant's nonce first.
    """
    first, second = (
        (panel_nonce, ha_nonce) if role == ROLE_HA else (ha_nonce, panel_nonce)
    )
    label = f"nesto-auth-{role}-v1".encode()
    return hmac_sha512(key, label + first + second + panel_id.encode("ascii"))


def session_keys(
    key: bytes, panel_nonce: bytes, ha_nonce: bytes
) -> tuple[bytes, bytes]:
    """Return the session keys: Home Assistant to panel, then panel to it."""
    salt = panel_nonce + ha_nonce
    return (
        hkdf_sha512(key, salt, b"nesto-session-h2p-v1"),
        hkdf_sha512(key, salt, b"nesto-session-p2h-v1"),
    )


# Frames, sections 3 and 7.3 ------------------------------------------------


def frame(payload: bytes) -> bytes:
    """Return a plaintext frame: a 4-byte big-endian length, then the payload."""
    if not 0 < len(payload) <= MAX_FRAME:
        raise ProtocolError(f"payload of {len(payload)} bytes")
    return struct.pack(">I", len(payload)) + payload


def frame_length(header: bytes) -> int:
    """Return the length a frame's header announces, checked against the limit."""
    (length,) = struct.unpack(">I", header)
    if not 0 < length <= MAX_FRAME:
        raise ProtocolError(f"frame of {length} bytes")
    return length


def frame_nonce(counter: int) -> bytes:
    """Return the 12-byte nonce for a frame: 4 zero bytes, a 64-bit LE counter."""
    return bytes(4) + struct.pack("<Q", counter)


def seal(key: bytes, counter: int, plaintext: bytes) -> bytes:
    """Return an encrypted frame: the length, then ciphertext and tag.

    The length field is the associated data, so it can't be changed in transit.
    """
    if not 0 < len(plaintext) + TAG_SIZE <= MAX_FRAME:
        raise ProtocolError(f"payload of {len(plaintext)} bytes")
    length = struct.pack(">I", len(plaintext) + TAG_SIZE)
    return length + ChaCha20Poly1305(key).encrypt(
        frame_nonce(counter), plaintext, length
    )


def open_frame(key: bytes, counter: int, data: bytes) -> bytes:
    """Decrypt a whole encrypted frame, its length included."""
    (length,) = struct.unpack(">I", data[:HEADER_SIZE])
    if length != len(data) - HEADER_SIZE or length < TAG_SIZE:
        raise ProtocolError("bad frame length")
    try:
        return ChaCha20Poly1305(key).decrypt(
            frame_nonce(counter), data[HEADER_SIZE:], data[:HEADER_SIZE]
        )
    except InvalidTag as err:
        raise ProtocolError("a frame failed to decrypt") from err
