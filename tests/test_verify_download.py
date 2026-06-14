"""Exit-code tests for solar_monitor.update.verify_download — the grandfather
-vs-abort decision wattpost-update keys off before swapping in a new release.
This is the brick-vs-proceed logic, so every path is pinned here.
"""
import base64
import json
import os
import tempfile

import nacl.signing
import pytest

from solar_monitor.update import release_verify as rv
from solar_monitor.update import verify_download as vd


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _sign(version: str, sha: str, channel: str, sk) -> str:
    m = {"version": version, "sha256": sha, "channel": channel}
    return _b64(sk.sign(rv.canonical_manifest(m)).signature)


def _write(obj) -> str:
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(p, "w", encoding="utf-8") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f)
    return p


@pytest.fixture
def sk(monkeypatch):
    """A test signing key pinned as THE release key, with a low current
    version so v0.1.200 manifests read as upgrades unless a test says otherwise."""
    key = nacl.signing.SigningKey.generate()
    monkeypatch.setattr(rv, "RELEASE_PUBKEY_B64", _b64(bytes(key.verify_key)))
    monkeypatch.setattr(rv, "PUBKEY_FILE", __import__("pathlib").Path("/no/such/pubkey"))
    monkeypatch.setattr("solar_monitor.__version__", "0.1.100", raising=False)
    return key


def _manifest(sk, version="v0.1.200", sha="abc123", channel="stable", signed=True):
    return {
        "version": version, "sha256": sha, "channel": channel,
        "signature": _sign(version, sha, channel, sk) if signed else "",
    }


def test_valid_release_ok(sk):
    m = _manifest(sk, sha="deadbeef")
    assert vd.verify_download(_write(m), "deadbeef") == vd.OK


def test_sha_mismatch_aborts(sk):
    m = _manifest(sk, sha="deadbeef")
    assert vd.verify_download(_write(m), "not-the-same-sha") == vd.SHA_MISMATCH


def test_bad_signature_aborts(sk):
    m = _manifest(sk, sha="deadbeef")
    m["signature"] = "AAAA"  # well-formed b64 but not a valid sig
    assert vd.verify_download(_write(m), "deadbeef") == vd.BAD_SIGNATURE


def test_unsigned_grandfathers(sk):
    m = _manifest(sk, sha="deadbeef", signed=False)
    assert vd.verify_download(_write(m), "deadbeef") == vd.GRANDFATHER


def test_no_pinned_key_grandfathers(sk, monkeypatch):
    # A box still on a pre-signing version: validly signed manifest, but no
    # trust anchor locally -> must grandfather, NOT abort (else it bricks the
    # very update that first ships the key).
    monkeypatch.setattr(rv, "RELEASE_PUBKEY_B64", "")
    m = _manifest(sk, sha="deadbeef")  # signed, but the box can't verify
    assert vd.verify_download(_write(m), "deadbeef") == vd.GRANDFATHER


def test_signed_downgrade_aborts(sk, monkeypatch):
    monkeypatch.setattr("solar_monitor.__version__", "0.1.300", raising=False)
    m = _manifest(sk, version="v0.1.200", sha="deadbeef")  # older than current
    assert vd.verify_download(_write(m), "deadbeef") == vd.DOWNGRADE


def test_malformed_json_grandfathers(sk):
    assert vd.verify_download(_write("{not json"), "deadbeef") == vd.GRANDFATHER


def test_missing_file_grandfathers(sk):
    assert vd.verify_download("/no/such/manifest.json", "deadbeef") == vd.GRANDFATHER


def test_cli_misuse_grandfathers():
    assert vd.main([]) == vd.GRANDFATHER
    assert vd.main(["only-one-arg"]) == vd.GRANDFATHER
