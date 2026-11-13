"""The protocol module against the test vectors the firmware is checked against.

tests/vectors/protocol-v1.json is a copy of the file in nesto-firmware
(tests/vectors/), generated there from its reference implementation. Copy it
again whenever the firmware's changes; never edit it here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.nesto import protocol as p

VECTORS = json.loads(
    (Path(__file__).parent / "vectors" / "protocol-v1.json").read_text("utf-8")
)
PAIRING = VECTORS["pairing"]
SESSION = VECTORS["session"]


def h(text: str) -> bytes:
    return bytes.fromhex(text)


def test_vectors_are_version_1():
    assert VECTORS["protocol"] == 1


def test_pairing_matches_the_vectors():
    ha_pub = p.public_key(h(PAIRING["ha_private"]))
    panel_pub = p.public_key(h(PAIRING["panel_private"]))
    assert ha_pub == h(PAIRING["ha_public"])
    assert panel_pub == h(PAIRING["panel_public"])

    shared = p.shared_secret(h(PAIRING["ha_private"]), panel_pub)
    assert shared == h(PAIRING["shared_secret"])
    assert p.shared_secret(h(PAIRING["panel_private"]), ha_pub) == shared

    assert p.pairing_code(ha_pub, panel_pub, shared) == PAIRING["code"]
    key = p.pairing_key(shared, ha_pub, panel_pub)
    assert key == h(PAIRING["pairing_key"])
    assert p.confirm_mac(key, p.ROLE_HA) == h(PAIRING["confirm_mac_ha"])
    assert p.confirm_mac(key, p.ROLE_PANEL) == h(PAIRING["confirm_mac_panel"])


def test_an_all_zero_shared_secret_is_refused():
    # A low-order point: X25519 with it gives all zeros, whatever the key.
    with pytest.raises(p.ProtocolError):
        p.shared_secret(h(PAIRING["ha_private"]), bytes(32))


def test_a_malformed_public_key_is_refused():
    with pytest.raises(p.ProtocolError):
        p.shared_secret(h(PAIRING["ha_private"]), bytes(31))


def test_authentication_matches_the_vectors():
    key = h(SESSION["pairing_key"])
    panel_nonce, ha_nonce = h(SESSION["panel_nonce"]), h(SESSION["ha_nonce"])
    panel_id = SESSION["panel_id"]

    assert p.auth_mac(key, p.ROLE_HA, panel_nonce, ha_nonce, panel_id) == h(
        SESSION["auth_mac_ha"]
    )
    assert p.auth_mac(key, p.ROLE_PANEL, panel_nonce, ha_nonce, panel_id) == h(
        SESSION["auth_mac_panel"]
    )
    assert p.session_keys(key, panel_nonce, ha_nonce) == (
        h(SESSION["key_h2p"]),
        h(SESSION["key_p2h"]),
    )


@pytest.mark.parametrize(
    "vector", SESSION["frames"], ids=lambda v: f"{v['direction']}-{v['counter']}"
)
def test_frames_match_the_vectors(vector):
    key = h(SESSION["key_h2p"] if vector["direction"] == "h2p" else SESSION["key_p2h"])
    plaintext = vector["plaintext"].encode()

    assert p.seal(key, vector["counter"], plaintext) == h(vector["frame"])
    assert p.open_frame(key, vector["counter"], h(vector["frame"])) == plaintext


def test_a_frame_with_the_wrong_counter_fails():
    vector = SESSION["frames"][0]
    with pytest.raises(p.ProtocolError):
        p.open_frame(h(SESSION["key_h2p"]), vector["counter"] + 1, h(vector["frame"]))


def test_a_frame_with_a_changed_length_fails():
    frame = h(SESSION["frames"][0]["frame"])
    with pytest.raises(p.ProtocolError):
        p.open_frame(h(SESSION["key_h2p"]), 0, frame + b"x")


def test_rfc_8439_example():
    """The AEAD itself: RFC 8439 section 2.8.2."""
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

    rfc = VECTORS["rfc8439"]
    sealed = ChaCha20Poly1305(h(rfc["key"])).encrypt(
        h(rfc["nonce"]), h(rfc["plaintext"]), h(rfc["aad"])
    )
    assert sealed == h(rfc["ciphertext_and_tag"])


def test_plain_frames():
    assert p.frame(b'{"t":"x"}') == b"\x00\x00\x00\x09" + b'{"t":"x"}'
    assert p.frame_length(b"\x00\x00\x00\x09") == 9


@pytest.mark.parametrize("size", [0, p.MAX_FRAME + 1])
def test_frame_limits(size):
    with pytest.raises(p.ProtocolError):
        p.frame(bytes(size))
    with pytest.raises(p.ProtocolError):
        p.frame_length(size.to_bytes(4, "big"))


def test_an_encrypted_frame_over_the_limit_is_refused():
    with pytest.raises(p.ProtocolError):
        p.seal(bytes(32), 0, bytes(p.MAX_FRAME))
