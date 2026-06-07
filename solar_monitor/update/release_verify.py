"""Release-signature verification for the auto-apply path (Phase C, cloud#15).

Auto-applying an update means the box runs code it fetched itself, so the
release must be *authenticated*, not merely integrity-checked. `wattpost-update`
already verifies the tarball SHA256 (integrity — detects corruption); this adds
*authenticity* — an Ed25519 signature, by WattPost's release key, over a
canonical manifest that pins the version + the tarball SHA256. Integrity alone
isn't enough: an attacker who can swap the release asset can swap its SHA too.

Trust anchor: the release **public** key, pinned in the image. The matching
private key lives only in the release pipeline — never on an appliance, never
in this repo. Verification is **fail-closed**: no pinned key, no/bad signature,
or any error => not verified => the auto-apply path refuses to swap. A box with
no pinned key simply never auto-applies (the safe default until release signing
is provisioned — see scripts/sign_release.py).

Mirrors the Ed25519 + canonical-JSON conventions in signed_audit.py.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import nacl.signing
from nacl.exceptions import BadSignatureError, CryptoError

log = logging.getLogger(__name__)

# Release public key (base64 of the raw 32-byte Ed25519 key), pinned at build
# time. EMPTY until the release keypair is provisioned — empty => fail-closed,
# the box never auto-applies. Generate with `scripts/sign_release.py --genkey`.
RELEASE_PUBKEY_B64 = ""

# Optional override file: lets the image ship/rotate a key without a code
# change. Takes precedence over the baked-in constant when present + non-empty.
PUBKEY_FILE = Path("/etc/wattpost/release-pubkey")


def _b64decode(s: str) -> bytes:
    """URL-safe base64 decode tolerant of missing padding (matches how the
    rest of the codebase emits signatures: urlsafe_b64encode().rstrip('='))."""
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canonical_manifest(manifest: dict) -> bytes:
    """The byte-exact form of the manifest that gets signed/verified. Sorted
    keys + compact separators so the signer and verifier agree to the byte."""
    return json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def pinned_pubkey() -> str | None:
    """The trusted release public key (base64), or None if unprovisioned.
    The override file wins over the baked-in constant."""
    try:
        if PUBKEY_FILE.exists():
            v = PUBKEY_FILE.read_text().strip()
            if v:
                return v
    except OSError:
        pass
    return RELEASE_PUBKEY_B64 or None


def verify_release(
    manifest: dict, signature_b64: str, pubkey_b64: str | None = None
) -> bool:
    """True iff `signature_b64` is a valid Ed25519 signature over
    canonical_manifest(manifest) by the trusted release key.

    Fail-closed: a missing pinned key, an empty/garbled signature, a wrong key,
    or any crypto error returns False and never raises — callers can gate the
    swap on a plain bool.
    """
    key = pubkey_b64 or pinned_pubkey()
    if not key:
        log.warning("release verify: no pinned release key — refusing (fail-closed)")
        return False
    if not signature_b64:
        log.warning("release verify: manifest has no signature — refusing")
        return False
    try:
        vk = nacl.signing.VerifyKey(_b64decode(key))
        vk.verify(canonical_manifest(manifest), _b64decode(signature_b64))
        return True
    except (BadSignatureError, CryptoError, ValueError, TypeError) as e:
        log.warning("release verify: signature check failed: %s", e)
        return False
