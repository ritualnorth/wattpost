"""Ed25519 signing + verification for backup tarballs (#297-3).

The threat model for cloud-stored backups is "compromised cloud
account swaps the bytes under a victim appliance's row, then queues
a restore". Even with the restore-time config sanitiser (#297-1)
that path is still bad, a malicious SQLite (with crafted device
rows, attacker rules, etc.) lands on the victim.

Mitigation: at upload time the appliance signs the archive bytes
with its Phase 1 (#303) ed25519 keypair. The signature + pubkey
fingerprint travel as HTTP headers and are persisted on the cloud
side (migration 0051). At restore time the appliance fetches the
signature back, verifies it against its OWN public key, and refuses
to apply a backup whose signature doesn't match.

Old backups taken before this rollout have no signature; the
restore path grandfathers them (logs a warning, allows the restore).
Once a customer has at least one signed backup, the unsigned ones
naturally age out of the retention window.

Notes:
* The signature covers the RAW tarball bytes, we don't tweak the
  archive layout (no embedded MANIFEST.sig member; that would make
  the bytes-being-signed circular). External-only sidecar is
  simpler and works for both cloud and local-file restore flows.
* PyNaCl is already a dep via the keypair module, no new pip pin.
"""
from __future__ import annotations

import base64
import logging
from typing import NamedTuple

from ..auth import keypair as _keypair

log = logging.getLogger(__name__)


# Algorithm tag emitted as `X-WP-Backup-Sig-Alg`. Currently constant;
# carried in the header so a future curve switch (e.g. Phase 10 ATECC
# secp256r1 hardware-key) can be rejected loudly by an older appliance
# rather than mis-verifying with the wrong primitive.
SIG_ALG = "ed25519"


class Signature(NamedTuple):
    sig_b64: str
    pubkey_fp: str
    alg: str = SIG_ALG


def sign_archive(tar_bytes: bytes) -> Signature:
    """Sign archive bytes with the appliance keypair. Returns the
    base64url-encoded signature + the appliance pubkey fingerprint
    suitable for the HTTP transport headers.

    Raises if the keypair can't be loaded, backup upload should
    still proceed in that case (the cloud handles missing signature
    headers gracefully) so callers should catch + log."""
    kp = _keypair.load_or_create()
    sig = kp.sign(tar_bytes)
    return Signature(
        sig_b64=base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii"),
        pubkey_fp=kp.fingerprint,
        alg=SIG_ALG,
    )


class BackupSigError(Exception):
    """Raised when verify_archive can't trust the signature. Caller
    must refuse to apply the restore on this error."""


def verify_archive(
    tar_bytes: bytes,
    *,
    sig_b64: str | None,
    pubkey_fp: str | None,
    alg: str | None,
) -> None:
    """Verify a tarball signature against the appliance's CURRENT
    keypair. Raises BackupSigError on any failure path:

      * Algorithm tag unrecognised (refuse: don't risk mis-verify).
      * Pubkey fingerprint doesn't match this appliance.
      * Signature is malformed or fails ed25519 verify.

    Tolerant of None for ALL three fields: that's the grandfather
    path for backups taken pre-0.1.99. Caller decides whether to
    accept the grandfather (cloud restore: warn + allow). When ANY
    of the three is present, we require ALL of them and they must
    all check out, partial signature data is treated as tampering.
    """
    # Grandfather: pre-0.1.99 backups have no signature. Caller logs
    # a warning and may allow the restore; we don't fail here.
    if sig_b64 is None and pubkey_fp is None and alg is None:
        return

    if not (sig_b64 and pubkey_fp and alg):
        raise BackupSigError(
            "partial signature data, refusing restore (looks tampered)"
        )

    if alg != SIG_ALG:
        raise BackupSigError(
            f"unrecognised signature alg {alg!r}, refusing restore"
        )

    kp = _keypair.load_or_create()
    if pubkey_fp != kp.fingerprint:
        raise BackupSigError(
            f"backup signed by keypair fp={pubkey_fp[:8]}... but this "
            f"appliance currently holds fp={kp.fingerprint[:8]}...; "
            "either this isn't our backup, or the appliance keypair "
            "has rotated since the backup was taken"
        )

    # Decode the b64url signature (tolerating missing padding).
    try:
        pad = "=" * ((4 - len(sig_b64) % 4) % 4)
        sig = base64.urlsafe_b64decode(sig_b64 + pad)
    except Exception as e:
        raise BackupSigError(f"signature base64 decode failed: {e}")

    try:
        kp.verify_key.verify(tar_bytes, sig)
    except Exception as e:
        raise BackupSigError(f"ed25519 verify failed: {e}")
