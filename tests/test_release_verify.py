"""Tests for release-signature verification (Phase C auto-apply, cloud#15).

The auto-apply path runs fetched code, so authenticity is load-bearing.
These cover the happy path plus the attacks that matter: a swapped tarball
hash, a wrong signing key, an unprovisioned (no pinned key) box, and an
empty signature — all of which must fail *closed*.
"""
import base64

import nacl.signing

from solar_monitor.update import release_verify as rv


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _sign(manifest: dict, sk: nacl.signing.SigningKey) -> str:
    return _b64(sk.sign(rv.canonical_manifest(manifest)).signature)


def _manifest() -> dict:
    return {"version": "v0.1.200", "sha256": "abc123", "channel": "stable"}


def test_valid_signature_verifies():
    sk = nacl.signing.SigningKey.generate()
    pub = _b64(bytes(sk.verify_key))
    m = _manifest()
    assert rv.verify_release(m, _sign(m, sk), pubkey_b64=pub) is True


def test_tampered_tarball_hash_fails():
    # The core attack: keep the signature, swap the SHA256 to point at a
    # malicious tarball. Must not verify.
    sk = nacl.signing.SigningKey.generate()
    pub = _b64(bytes(sk.verify_key))
    m = _manifest()
    sig = _sign(m, sk)
    tampered = dict(m, sha256="deadbeef")
    assert rv.verify_release(tampered, sig, pubkey_b64=pub) is False


def test_wrong_key_fails():
    signer = nacl.signing.SigningKey.generate()
    attacker = nacl.signing.SigningKey.generate()
    m = _manifest()
    sig = _sign(m, signer)
    assert rv.verify_release(m, sig, pubkey_b64=_b64(bytes(attacker.verify_key))) is False


def test_no_pinned_key_is_fail_closed(monkeypatch, tmp_path):
    # Unprovisioned box: no baked constant, no override file -> never applies.
    monkeypatch.setattr(rv, "RELEASE_PUBKEY_B64", "")
    monkeypatch.setattr(rv, "PUBKEY_FILE", tmp_path / "absent-release-pubkey")
    sk = nacl.signing.SigningKey.generate()
    m = _manifest()
    assert rv.verify_release(m, _sign(m, sk)) is False


def test_pubkey_file_override_used(monkeypatch, tmp_path):
    # The override file is the trust anchor when present.
    sk = nacl.signing.SigningKey.generate()
    keyfile = tmp_path / "release-pubkey"
    keyfile.write_text(_b64(bytes(sk.verify_key)))
    monkeypatch.setattr(rv, "RELEASE_PUBKEY_B64", "")
    monkeypatch.setattr(rv, "PUBKEY_FILE", keyfile)
    m = _manifest()
    assert rv.verify_release(m, _sign(m, sk)) is True


def test_empty_signature_fails():
    sk = nacl.signing.SigningKey.generate()
    m = _manifest()
    assert rv.verify_release(m, "", pubkey_b64=_b64(bytes(sk.verify_key))) is False


def test_garbage_signature_fails():
    sk = nacl.signing.SigningKey.generate()
    m = _manifest()
    assert rv.verify_release(m, "!!!not-base64!!!", pubkey_b64=_b64(bytes(sk.verify_key))) is False
