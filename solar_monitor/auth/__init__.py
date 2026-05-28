"""Identity v2, appliance-side auth primitives.

This package owns the per-appliance ed25519 keypair (the trust
root for the new identity layer described in
docs/architecture/identity-v2.md) plus the helpers cloud + appliance
code reach into for signing and verification.

Phase 1 (#303) ships the keypair foundation:
  * Generate ed25519 on first use; persist private key sealed with
    a libsodium SecretBox keyed off a machine-anchored secret.
  * Public key + fingerprint exposed to callers (cloud uploads it
    at pair time; future phases sign / verify against it).
  * Load-on-boot is idempotent; the daemon never blocks on it.

Later phases (see RFC) wire signed cloud→appliance commands,
JWT verification, mTLS, and hardware-backed key storage on top.
"""
from .keypair import (
    Keypair,
    KeypairError,
    load_or_create,
    public_key_b64,
    fingerprint,
)

__all__ = [
    "Keypair",
    "KeypairError",
    "load_or_create",
    "public_key_b64",
    "fingerprint",
]
