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

from solar_monitor.cloud import command_verify as cv

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
