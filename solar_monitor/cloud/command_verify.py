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

Behaviour:
  * Signed cmd, sig verifies → ok, dispatch
  * Signed cmd, sig FAILS    → reject, refuse to dispatch, log
                                + signed_audit.write_event so
                                the rejection becomes a tamper-
                                evident record
  * Unsigned cmd (no kid/sig/nonce, pre-rollout cloud, or a row
    inserted before migration 0052) → grandfather: dispatch with a
    warning. Once the cloud has rolled out 0052 + signing for ~1
    deploy cycle, we can flip the default to reject-unsigned.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..auth import oidc_config, oidc_rp

log = logging.getLogger(__name__)


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


async def verify_command(cmd: dict[str, Any], *, appliance_id: int) -> bool:
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

    # Grandfather path, fully unsigned command. Common during the
    # transition; warn but allow.
    if not (sig_b64 or kid or nonce):
        log.warning(
            "command_verify: cmd id=%s has NO signature, grandfathering "
            "(cloud pre-0.1.105 or signing key absent)",
            cmd.get("id"),
        )
        return True

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
        # Appliance pre-upgrade or post-pair-but-no-OIDC-yet. We have
        # no JWKS to verify against. Grandfather rather than refuse,
        # the alternative is bricking commands on transitional boxes.
        log.warning(
            "command_verify: no oidc_config (pre-upgrade?), "
            "grandfathering cmd id=%s", cmd.get("id"),
        )
        return True

    try:
        qat_dt = datetime.fromisoformat(str(queued_at))
        if qat_dt.tzinfo is None:
            qat_dt = qat_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("command_verify: cmd id=%s malformed queued_at", cmd.get("id"))
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

    vk = oidc_rp._verify_key_from_jwk(match)
    if vk is None:
        log.warning("command_verify: unsupported JWK shape for kid %r, refusing", kid)
        return False

    try:
        pad = "=" * ((4 - len(sig_b64) % 4) % 4)
        sig = base64.urlsafe_b64decode(sig_b64 + pad)
        vk.verify(repr_str.encode("utf-8"), sig)
    except Exception as e:
        log.warning("command_verify: sig verify FAILED for cmd id=%s (%s), refusing",
                    cmd.get("id"), e)
        return False

    log.debug("command_verify: cmd id=%s sig ok (kid=%s)", cmd.get("id"), kid[:8])
    return True
