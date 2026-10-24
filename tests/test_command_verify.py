"""Command verification: forged, unsigned, stale and replayed commands are refused.

The signing key is a real ed25519 key; the JWKS fetch is stubbed. The cloud
side of the format lives in wattpost-cloud/command_signing.py and must stay
byte-identical (see canonical_repr / signing_input).
"""
import asyncio
import base64
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from nacl.signing import SigningKey

import pytest

from solar_monitor.cloud import command_verify as cv


@pytest.fixture(autouse=True)
def _private_state_dir(tmp_path, monkeypatch):
    """Keep the signing-key pin (and ledger) out of the real state directory."""
    monkeypatch.setenv("WATTPOST_DB", str(tmp_path / "db.sqlite"))

APPLIANCE = 7
KID = "kid-1"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Env:
    def __init__(self, monkeypatch=None):
        self.sk = SigningKey.generate()
        self.jwk = {"kty": "OKP", "crv": "Ed25519", "kid": KID,
                    "x": _b64u(bytes(self.sk.verify_key))}
        self.orig = (cv.oidc_config.load, cv.oidc_rp.fetch_jwks)
        cv.oidc_config.load = lambda: SimpleNamespace(jwks_url="https://x/jwks")

        async def fake_fetch(url, force=False):
            return [self.jwk]
        cv.oidc_rp.fetch_jwks = fake_fetch

    def restore(self):
        cv.oidc_config.load, cv.oidc_rp.fetch_jwks = self.orig

    def command(self, *, cmd_id=1, kind="backup_now", appliance_id=APPLIANCE, queued_at=None,
                payload=None, sign_key=None, nonce="n0nce", domain=True):
        qat = queued_at or datetime.now(timezone.utc)
        repr_str = cv.canonical_repr(
            cmd_id=cmd_id, appliance_id=appliance_id, kind=kind, target_version=None,
            target_backup_id=None, payload_json=payload, queued_at=qat, nonce=nonce)
        msg = cv.signing_input(repr_str) if domain else repr_str.encode()
        sig = (sign_key or self.sk).sign(msg).signature
        return {"id": cmd_id, "kind": kind, "target_version": None, "target_backup_id": None,
                "payload_json": payload, "queued_at": qat.isoformat(), "nonce": nonce,
                "signing_kid": KID, "signature_b64": _b64u(sig), "appliance_id": appliance_id}


def run(coro):
    return asyncio.run(coro)


def verify(env, cmd, appliance_id=APPLIANCE):
    return run(cv.verify_command(cmd, appliance_id=appliance_id))


def test_valid_command_passes():
    env = Env()
    try:
        assert verify(env, env.command()) is True
    finally:
        env.restore()


def test_unsigned_command_is_refused():
    """The attack this exists for: a row inserted straight into the cloud DB."""
    env = Env()
    try:
        c = env.command()
        for k in ("signature_b64", "signing_kid", "nonce"):
            c.pop(k)
        assert verify(env, c) is False
        c2 = {"id": 9, "kind": "restore_from_cloud", "queued_at": datetime.now(timezone.utc).isoformat()}
        assert verify(env, c2) is False
    finally:
        env.restore()


def test_partial_signature_is_refused():
    env = Env()
    try:
        c = env.command(); c.pop("nonce")
        assert verify(env, c) is False
    finally:
        env.restore()


def test_no_oidc_config_means_refuse_not_trust():
    env = Env()
    try:
        cv.oidc_config.load = lambda: None
        assert verify(env, env.command()) is False
    finally:
        env.restore()


def test_tampered_fields_are_refused():
    env = Env()
    try:
        for field, value in (("kind", "restore_from_cloud"), ("payload_json", '{"x":1}'),
                             ("id", 2), ("target_version", "9.9.9"), ("nonce", "other")):
            c = env.command()
            c[field] = value
            assert verify(env, c) is False, field
    finally:
        env.restore()


def test_command_for_another_appliance_is_refused():
    env = Env()
    try:
        assert verify(env, env.command(appliance_id=8), appliance_id=APPLIANCE) is False
    finally:
        env.restore()


def test_wrong_key_is_refused():
    env = Env()
    try:
        assert verify(env, env.command(sign_key=SigningKey.generate())) is False
    finally:
        env.restore()


def test_signature_without_domain_tag_is_refused():
    """A signature over the bare canonical JSON (what a JWT-signing oracle or an
    old cloud could produce) must not be accepted as a command."""
    env = Env()
    try:
        assert verify(env, env.command(domain=False)) is False
    finally:
        env.restore()


def test_stale_and_future_commands_are_refused():
    env = Env()
    try:
        now = datetime.now(timezone.utc)
        assert verify(env, env.command(queued_at=now - timedelta(days=6))) is True
        assert verify(env, env.command(queued_at=now - timedelta(days=8))) is False
        assert verify(env, env.command(queued_at=now + timedelta(minutes=2))) is True   # RTC skew
        assert verify(env, env.command(queued_at=now + timedelta(hours=1))) is False
    finally:
        env.restore()


def test_non_utc_offset_cannot_stretch_the_replay_window():
    """canonical_repr drops the offset, so rewriting it keeps the signature
    valid. A command signed 7d10h ago re-labelled -12:00 would look 6d22h old."""
    env = Env()
    try:
        old = datetime.now(timezone.utc) - timedelta(days=7, hours=10)
        c = env.command(queued_at=old)
        assert verify(env, c) is False                      # stale as signed
        c["queued_at"] = old.replace(tzinfo=None).isoformat() + "-12:00"
        assert verify(env, c) is False
        for off in ("+05:30", "-00:30", "+14:00"):
            fresh = env.command(queued_at=datetime.now(timezone.utc))
            fresh["queued_at"] = fresh["queued_at"].replace("+00:00", off)
            assert verify(env, fresh) is False, off
        # an explicit zero offset and a naive stamp are still UTC
        fresh = env.command(queued_at=datetime.now(timezone.utc))
        assert verify(env, fresh) is True
        fresh["queued_at"] = fresh["queued_at"].replace("+00:00", "")
        assert verify(env, fresh) is True
    finally:
        env.restore()


def test_unknown_kid_is_refused():
    env = Env()
    try:
        c = env.command(); c["signing_kid"] = "other"
        assert verify(env, c) is False
    finally:
        env.restore()


# ---- ledger ----------------------------------------------------------------

def _ledger(cap=1000):
    d = tempfile.mkdtemp()
    return cv.CommandLedger(os.path.join(d, "ledger.json"), cap=cap)


def test_ledger_blocks_replay_of_completed_commands_across_restarts():
    led = _ledger()
    assert not led.seen(5)
    led.record(5)
    assert led.seen(5)
    again = cv.CommandLedger(led.path)           # "restart"
    assert again.seen(5) and not again.seen(6)


def test_ledger_does_not_block_in_flight_commands():
    """Only terminal commands are recorded, so a command mid-update can be
    re-presented by the cloud."""
    led = _ledger()
    assert not led.seen(10)


def test_ledger_is_bounded_and_eviction_cannot_reopen_old_ids():
    led = _ledger(cap=3)
    for i in (10, 11, 12, 13):
        led.record(i)
    assert led._ids == [11, 12, 13]
    assert led.seen(10), "evicted id below the floor must still be refused"
    assert led.seen(11) and not led.seen(14)


def test_ledger_survives_corrupt_file():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "l.json")
    open(p, "w").write("not json")
    led = cv.CommandLedger(p)
    assert not led.seen(1)
    led.record(1)
    assert cv.CommandLedger(p).seen(1)


def test_ledger_fsyncs_file_and_directory(monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    led = _ledger()
    led.record(1)
    assert len(calls) >= 2          # the tmp file, then the directory


def test_ledger_keeps_a_bak_and_recovers_from_a_corrupt_main_file():
    led = _ledger()
    led.record(1)
    led.record(2)
    assert os.path.exists(led.path + ".bak")
    open(led.path, "w").write("{trunc")             # torn write
    again = cv.CommandLedger(led.path)
    assert again.seen(1)                            # recovered from .bak, replay still blocked
    # both files gone/corrupt -> empty, not a crash
    open(led.path + ".bak", "w").write("garbage")
    assert not cv.CommandLedger(led.path).seen(1)
    # a corrupt main must not clobber a good .bak with garbage on the next record
    led2 = _ledger()
    led2.record(7); led2.record(8)
    open(led2.path, "w").write("zzz")
    r = cv.CommandLedger(led2.path)
    r.record(9)
    assert cv.CommandLedger(led2.path).seen(7) and cv.CommandLedger(led2.path).seen(9)


# ---- cross-repo golden vector (same one lives in wattpost-cloud) -------------

def test_golden_vector_matches_the_cloud_byte_for_byte():
    import hashlib
    r = cv.canonical_repr(
        cmd_id=42, appliance_id=7, kind="update", target_version="0.2.0", target_backup_id=None,
        payload_json='{"a":"é"}',
        queued_at=datetime(2026, 10, 24, 9, 30, 15, 123456, tzinfo=timezone.utc),
        nonce="AAAAAAAAAAAAAAAAAAAAAA")
    assert hashlib.sha256(cv.signing_input(r)).hexdigest() == \
        "3e1d953f55c0c60d02c1521d51571c596b6cf8a9bb95426dacdcf1539e3b4e66"


# ---- strings inside a (validly signed) command --------------------------------

def test_version_fields_must_look_like_release_versions():
    from solar_monitor.cloud.service import _bad_version_field as bad
    assert bad({"target_version": "0.1.188"}) is None
    assert bad({"target_version": "v0.1.188-rc.1"}) is None
    assert bad({"payload_json": '{"version": "0.2.0"}'}) is None
    assert bad({}) is None
    for evil in ("0.1.1; rm -rf /", "$(reboot)", "latest", "../../x", "0.1.188 ", "1.2", "x" * 40):
        assert bad({"target_version": evil}), evil
        assert bad({"payload_json": '{"version": "%s"}' % evil}), evil
    assert bad({"payload_json": '{"image": "evil.example/x:1"}'}), "custom images are refused"
    assert bad({"payload_json": "not json"}) and bad({"payload_json": "[1]"})


# ---- signing-key pin (trust on first use) ----------------------------------

def test_first_verified_command_pins_the_key_and_a_new_key_is_then_refused():
    env = Env()
    try:
        assert verify(env, env.command(cmd_id=1)) is True
        assert cv.pin_state()["pinned"] is True
        # The cloud (or whoever controls its JWKS) now publishes a different
        # key under the same kid and signs with it: refused.
        evil = SigningKey.generate()
        env.jwk = {"kty": "OKP", "crv": "Ed25519", "kid": KID, "x": _b64u(bytes(evil.verify_key))}
        assert verify(env, env.command(cmd_id=2, sign_key=evil)) is False
        # ...and under a brand new kid.
        env.jwk = dict(env.jwk, kid="kid-2")
        c = env.command(cmd_id=3, sign_key=evil)
        c["signing_kid"] = "kid-2"
        assert verify(env, c) is False
    finally:
        env.restore()


def test_a_forged_command_cannot_plant_its_own_pin():
    env = Env()
    try:
        evil = SigningKey.generate()
        env.jwk = {"kty": "OKP", "crv": "Ed25519", "kid": KID, "x": _b64u(bytes(evil.verify_key))}
        bad = env.command(sign_key=SigningKey.generate())      # signed by neither
        assert verify(env, bad) is False
        assert cv.pin_state()["pinned"] is False               # nothing pinned by a failed verify
    finally:
        env.restore()


def test_pairing_pins_up_front_and_retrust_replaces_it():
    env = Env()
    try:
        cv.oidc_config.load = lambda: SimpleNamespace(jwks_url="https://x/jwks", endpoint="https://x")
        assert run(cv.pin_current_keys()) is True
        assert cv.pin_state() == {"pinned": True, "kids": [KID]}
        assert run(cv.pin_current_keys()) is False             # already pinned: no silent overwrite
        new = SigningKey.generate()
        env.jwk = {"kty": "OKP", "crv": "Ed25519", "kid": "kid-new", "x": _b64u(bytes(new.verify_key))}
        c = env.command(sign_key=new)
        c["signing_kid"] = "kid-new"
        assert verify(env, c) is False                         # new key refused until the owner re-trusts
        assert run(cv.pin_current_keys(force=True)) is True    # the owner's explicit action
        assert verify(env, c) is True
        assert cv.clear_pin() is True and cv.pin_state()["pinned"] is False
    finally:
        env.restore()
