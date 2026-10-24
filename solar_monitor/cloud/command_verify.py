"""Appliance-side verifier for cloud-signed commands (#299).

Cloud signs each command queued for this appliance with the active
OIDC ed25519 key. The appliance verifies the signature against the
cached cloud JWKS (already fetched per Phase 3 OIDC) before
dispatching. Closes the threat where a cloud DB compromise lets an
attacker INSERT a forged appliance_commands row.

canonical_repr MUST match cloud/wattpost_cloud/command_signing.py
byte-for-byte. Any drift means every cloud-signed command fails
verify here, treat the two functions as one logical unit when
making changes.

Behaviour (strict: there is no grandfathering):
  * Signature verifies, command is fresh, targets this appliance  -> dispatch
  * Anything else -> refuse, log, and write a signed-audit event so the
    rejection becomes a tamper-evident record. That includes commands with
    no signature at all: the threat this exists for is somebody who can
    write rows into the cloud database, and such a row is exactly an
    unsigned command, so accepting those would defeat the whole check.
  * A command older than MAX_AGE, or stamped in the future, is refused, so
    a captured signed command can't be replayed at will.
  * A command that already finished (CommandLedger) is refused, so a
    completed `restore_from_cloud` can't be re-run after a restart.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from ..auth import oidc_config, oidc_rp

log = logging.getLogger(__name__)

# Domain separation: the cloud signs OIDC tokens with the same ed25519 key,
# so commands carry a fixed prefix that no JWT signing input can have.
DOMAIN = b"wattpost-cmd-v2\n"

# A command may legitimately wait while the appliance is offline, but not
# indefinitely. Clock skew allowance covers an appliance with a drifting RTC.
MAX_AGE = timedelta(days=7)
MAX_FUTURE = timedelta(minutes=5)


# A command naming a kid we don't have forces a JWKS refetch (key rotation).
# Without a limit, a stream of commands with made-up kids turns every
# heartbeat into a pile of outbound fetches. At most one forced refetch per
# heartbeat (the caller's `refresh_state`) and one per minute overall.
FORCE_REFRESH_MIN_INTERVAL_S = 60.0
_last_forced_refresh: float | None = None


def reset_refresh_limiter() -> None:
    global _last_forced_refresh
    _last_forced_refresh = None


def signing_input(repr_str: str) -> bytes:
    """Bytes that are actually signed: domain tag + canonical JSON.
    Mirror in cloud command_signing.py."""
    return DOMAIN + repr_str.encode("utf-8")


def canonical_repr(
    *,
    cmd_id:           int,
    appliance_id:     int,
    kind:             str,
    target_version:   str | None,
    target_backup_id: int | None,
    payload_json:     str | None,
    queued_at:        datetime,
    nonce:            str,
) -> str:
    """MIRROR of cloud command_signing.canonical_repr. Any
    deviation = every signature fails. Keep in lockstep."""
    return json.dumps(
        {
            "id":               cmd_id,
            "issuer":           "cloud",
            "appliance_id":     appliance_id,
            "kind":             kind,
            "target_version":   target_version,
            "target_backup_id": target_backup_id,
            "payload_json":     payload_json,
            "queued_at":        queued_at.replace(tzinfo=timezone.utc).isoformat(),
            "nonce":            nonce,
        },
        sort_keys=True, separators=(",", ":"),
        ensure_ascii=False,
    )


class CommandVerifyError(Exception):
    """Raised by verify_command on a sig failure that means we
    MUST NOT dispatch this command."""


async def verify_command(cmd: dict[str, Any], *, appliance_id: int,
                         refresh_state: dict | None = None) -> bool:
    """Return True when the command is safe to dispatch.

    True paths:
      * Command has a valid signature against the cloud's published
        JWKS.
      * Command has NO signature at all (pre-rollout grandfather);
        a warning is logged so the count is visible.

    False paths:
      * Partial signature (some fields present, others NULL), likely
        tampering with the heartbeat payload mid-transit.
      * Unknown kid (mid-rotation we'll refresh JWKS once; still
        unknown → reject).
      * Bad signature.
      * Bad timestamp / nonce.

    Caller treats False as "skip this command + log + leave it
    queued cloud-side for re-evaluation when a real signing key is
    present"."""
    sig_b64   = cmd.get("signature_b64")
    kid       = cmd.get("signing_kid")
    nonce     = cmd.get("nonce")
    queued_at = cmd.get("queued_at")

    if not (sig_b64 or kid or nonce):
        log.warning("command_verify: cmd id=%s is UNSIGNED, refusing dispatch",
                    cmd.get("id"))
        return False

    # Partial signature → refuse.
    if not (sig_b64 and kid and nonce and queued_at):
        log.warning(
            "command_verify: cmd id=%s has PARTIAL signature "
            "(sig=%s kid=%s nonce=%s qat=%s), refusing dispatch",
            cmd.get("id"), bool(sig_b64), bool(kid), bool(nonce), bool(queued_at),
        )
        return False

    cfg = oidc_config.load()
    if cfg is None:
        # No JWKS to verify against, so nothing can be trusted. The
        # appliance registers its identity before its first heartbeat, so
        # this only happens before pairing completes.
        log.warning("command_verify: no oidc_config, refusing cmd id=%s", cmd.get("id"))
        return False

    try:
        qat_dt = datetime.fromisoformat(str(queued_at))
        if qat_dt.tzinfo is None:
            qat_dt = qat_dt.replace(tzinfo=timezone.utc)
        elif qat_dt.utcoffset() != timedelta(0):
            # canonical_repr drops the offset (it re-stamps the wall clock as
            # UTC), so a non-zero offset would keep the signature valid while
            # shifting the apparent age used below: a way to stretch the
            # replay window by up to ~14h. The cloud only ever signs UTC.
            log.warning("command_verify: cmd id=%s queued_at has a non-UTC offset, refusing",
                        cmd.get("id"))
            return False
    except ValueError:
        log.warning("command_verify: cmd id=%s malformed queued_at", cmd.get("id"))
        return False

    now = datetime.now(timezone.utc)
    if now - qat_dt > MAX_AGE:
        log.warning("command_verify: cmd id=%s is %s old (max %s), refusing",
                    cmd.get("id"), now - qat_dt, MAX_AGE)
        return False
    if qat_dt - now > MAX_FUTURE:
        log.warning("command_verify: cmd id=%s is dated %s in the future, refusing",
                    cmd.get("id"), qat_dt - now)
        return False

    repr_str = canonical_repr(
        cmd_id=int(cmd.get("id", 0)),
        appliance_id=appliance_id,
        kind=str(cmd.get("kind", "")),
        target_version=cmd.get("target_version"),
        target_backup_id=cmd.get("target_backup_id"),
        payload_json=cmd.get("payload_json"),
        queued_at=qat_dt,
        nonce=str(nonce),
    )

    # Fetch + cache JWKS; force-refresh once on unknown kid.
    try:
        keys = await oidc_rp.fetch_jwks(cfg.jwks_url)
        match = next((k for k in keys if k.get("kid") == kid), None)
        if match is None:
            global _last_forced_refresh
            now_m = time.monotonic()
            spent = refresh_state is not None and refresh_state.get("done")
            too_soon = (_last_forced_refresh is not None
                        and now_m - _last_forced_refresh < FORCE_REFRESH_MIN_INTERVAL_S)
            if spent or too_soon:
                log.warning("command_verify: unknown kid %r for cmd id=%s and a JWKS "
                            "refresh was already done recently, refusing",
                            kid, cmd.get("id"))
                return False
            _last_forced_refresh = now_m
            if refresh_state is not None:
                refresh_state["done"] = True
            keys = await oidc_rp.fetch_jwks(cfg.jwks_url, force=True)
            match = next((k for k in keys if k.get("kid") == kid), None)
    except Exception as e:
        log.warning("command_verify: JWKS fetch failed (%s), refusing cmd id=%s",
                    e, cmd.get("id"))
        return False
    if match is None:
        log.warning("command_verify: unknown kid %r for cmd id=%s, refusing",
                    kid, cmd.get("id"))
        return False

    if not _pin_allows(kid, match):
        log.error("command_verify: cmd id=%s is signed by key %r which is NOT the one "
                  "pinned at pairing; refusing. If the cloud was rebuilt or its key "
                  "changed on purpose, re-trust it in Settings -> Integrations.",
                  cmd.get("id"), kid)
        return False

    vk = oidc_rp._verify_key_from_jwk(match)
    if vk is None:
        log.warning("command_verify: unsupported JWK shape for kid %r, refusing", kid)
        return False

    try:
        pad = "=" * ((4 - len(sig_b64) % 4) % 4)
        sig = base64.urlsafe_b64decode(sig_b64 + pad)
        vk.verify(signing_input(repr_str), sig)
    except Exception as e:
        log.warning("command_verify: sig verify FAILED for cmd id=%s (%s), refusing",
                    cmd.get("id"), e)
        return False

    _pin_after_verified(kid, match)
    log.debug("command_verify: cmd id=%s sig ok (kid=%s)", cmd.get("id"), kid[:8])
    return True


class CommandLedger:
    """Ids of commands that have run to a terminal state, persisted so a
    restart doesn't forget them.

    Signature + freshness stop forgery and stale replay; the ledger stops
    replay of a command that already completed (a signed `restore_from_cloud`
    is valid for 7 days, and re-running it after a restart would roll the
    appliance back again). Commands still in flight are deliberately NOT
    recorded, so the cloud can keep re-presenting one that is mid-update.

    Bounded: it keeps the newest `cap` ids. Anything older than the oldest
    remembered id is refused too once the ledger is full, so eviction can't
    be used to re-open an old command.
    """

    def __init__(self, path: str, cap: int = 1000) -> None:
        self.path = path
        self.cap = cap
        self._ids: list[int] = self._read()

    @staticmethod
    def _load(path: str) -> list[int] | None:
        """ids from one file, or None if it is missing/corrupt."""
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, list):
                return None
            return sorted({int(x) for x in data})
        except (OSError, ValueError, TypeError):
            return None

    def _read(self) -> list[int]:
        ids = self._load(self.path)
        if ids is None:
            # Main file missing or corrupt (power cut mid-write on an SD
            # card): fall back to the previous good copy rather than
            # forgetting every completed command and re-opening replay.
            ids = self._load(self.path + ".bak")
            if ids is not None:
                log.warning("command ledger: %s unreadable, recovered from .bak", self.path)
        return (ids or [])[-self.cap:]

    def seen(self, cmd_id: int) -> bool:
        if cmd_id in self._ids:
            return True
        return len(self._ids) >= self.cap and cmd_id < self._ids[0]

    def record(self, cmd_id: int) -> None:
        if cmd_id in self._ids:
            return
        self._ids = sorted(set(self._ids) | {int(cmd_id)})[-self.cap:]
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._ids, f)
                f.flush()
                os.fsync(f.fileno())
            # Keep the previous good ledger as .bak (only if it parses).
            if self._load(self.path) is not None:
                try:
                    os.replace(self.path, self.path + ".bak")
                except OSError:
                    pass
            os.replace(tmp, self.path)
            try:                       # make the rename itself durable
                dfd = os.open(os.path.dirname(self.path) or ".", os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
        except OSError:
            log.exception("command ledger: could not persist")


def default_ledger_path() -> str:
    db = os.environ.get("WATTPOST_DB", "")
    base = os.path.dirname(db) if db else "/var/lib/wattpost"
    return os.path.join(base, "cloud_command_ledger.json")


# ---- signing-key pin (trust on first use) ---------------------------------
#
# The cloud publishes its command-signing keys in a JWKS. Without a pin, whoever
# can alter that document (a database compromise, a hijacked CDN or DNS record)
# can publish a key of their own and sign commands the appliance will run. So
# the appliance remembers the keys it saw at pairing and afterwards only
# accepts those. The cloud has one long-lived signing key and no rotation, so
# a new kid is exactly the situation worth refusing; the owner re-trusts it
# from Settings after checking it is a change they expect (a rebuilt cloud).

def default_pin_path() -> str:
    db = os.environ.get("WATTPOST_DB", "")
    base = os.path.dirname(db) if db else "/var/lib/wattpost"
    return os.path.join(base, "cloud_signing_pin.json")


def _read_pin(path: str) -> dict | None:
    try:
        with open(path) as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("keys"), dict):
            return d
    except (OSError, ValueError):
        pass
    return None


def _cloud_host() -> str:
    """Host of the cloud whose keys these are (from the saved OIDC config, which
    is pinned to the endpoint the owner configured)."""
    from ..urlcheck import host_of
    cfg = oidc_config.load()
    return (host_of(getattr(cfg, "jwks_url", "") or "") or "") if cfg else ""


def _write_pin(path: str, endpoint: str, keys: dict[str, str]) -> None:
    from ..fsutil import atomic_write_text
    atomic_write_text(path, json.dumps(
        {"endpoint": endpoint, "host": _cloud_host(), "keys": keys}, sort_keys=True))


def _pin_for_this_cloud(path: str) -> dict | None:
    """The pin, unless it was made for a different cloud (the owner re-paired
    to another host without unpairing): then it no longer applies."""
    d = _read_pin(path)
    if d is None:
        return None
    host = _cloud_host()
    if d.get("host") and host and d["host"] != host:
        return None
    return d


def clear_pin(path: str | None = None) -> bool:
    path = path or default_pin_path()
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False


def pin_state(path: str | None = None) -> dict:
    d = _read_pin(path or default_pin_path())
    return {"pinned": d is not None, "kids": sorted((d or {}).get("keys", {}))}


def _okp_keys(keys: list[dict]) -> dict[str, str]:
    return {k["kid"]: k["x"] for k in keys
            if k.get("kty") == "OKP" and k.get("crv") == "Ed25519"
            and isinstance(k.get("kid"), str) and isinstance(k.get("x"), str)}


async def pin_current_keys(*, force: bool = False, path: str | None = None) -> bool:
    """Pin the cloud's current signing keys. Called at pairing; no-op when a
    pin already exists unless `force` (the owner's explicit re-trust)."""
    path = path or default_pin_path()
    if not force and _pin_for_this_cloud(path) is not None:
        return False
    cfg = oidc_config.load()
    if cfg is None:
        return False
    keys = _okp_keys(await oidc_rp.fetch_jwks(cfg.jwks_url, force=True))
    if not keys:
        return False
    _write_pin(path, getattr(cfg, "endpoint", "") or "", keys)
    log.info("command_verify: pinned %d cloud signing key(s): %s", len(keys), sorted(keys))
    return True


def _pin_allows(kid: str, jwk: dict, path: str | None = None) -> bool:
    """True if this key may be used to verify a command: it is the pinned one,
    or there is no pin yet (an install that paired before pins existed)."""
    d = _pin_for_this_cloud(path or default_pin_path())
    return d is None or d["keys"].get(kid) == jwk.get("x")


def _pin_after_verified(kid: str, jwk: dict, path: str | None = None) -> None:
    """Trust-on-first-use for installs without a pin: record the key only
    once it has actually verified a command signature."""
    path = path or default_pin_path()
    if _pin_for_this_cloud(path) is not None:
        return
    keys = _okp_keys([jwk])
    if keys:
        try:
            cfg = oidc_config.load()
            _write_pin(path, getattr(cfg, "endpoint", "") or "", keys)
        except OSError:
            log.exception("command_verify: could not write the signing-key pin")
