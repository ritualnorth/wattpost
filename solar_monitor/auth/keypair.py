"""Appliance ed25519 keypair — generation, sealed storage, load.

Identity v2 Phase 1 (#303). See docs/architecture/identity-v2.md
for the full design — this module ships the appliance-side half.

What this file owns:

  * **Generation.** ed25519 via PyNaCl on first use. Random,
    no derivation from hardware so two appliances can never
    collide.
  * **Sealed-at-rest storage.** Private key encrypted with a
    `SecretBox` whose key is derived from a machine-anchored
    secret (`/etc/machine-id` if present, otherwise a long
    random file we own). HKDF-style derivation via BLAKE2b.
    Not equivalent to TPM-backed storage — Phase 10 (#312)
    swaps this for ATECC608A / YubiKey HSM where available —
    but raises the bar meaningfully against a casual disk-
    pull attack.
  * **Public key + fingerprint** exposed for cloud upload.
    Fingerprint = first 16 hex chars of SHA-256(public_key);
    used in JWT `wp_appliance_kid` claims to identify which
    appliance a token is bound to.

Threat-model context (RFC §"Threat model" T5):

  * Disk image stolen, daemon-shutdown state. Attacker reads
    the sealed key + machine-id, derives the box key, decrypts
    the private key. Mitigation: machine-id is not in any
    persistent backup we ship; user is alerted via audit log
    when a new keypair appears against the same appliance row.
  * Disk image stolen, daemon-running state. Process memory
    has the unsealed private key. Mitigation: hardware key
    storage in Phase 10. We don't pretend software-encrypted
    keys protect against root access.

The "first-use" generation runs deterministically against the
disk: subsequent boots load the existing key rather than mint
a new one. Replacing the keypair is a deliberate user action
(re-pair flow).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import nacl.encoding
import nacl.secret
import nacl.signing
from nacl.exceptions import CryptoError

log = logging.getLogger(__name__)


# Where keys live on disk. /var/lib/wattpost is created by install.sh
# with daemon ownership; Docker installs bind-mount this from the host
# so keys survive container recreation. Pattern mirrors install_id.py.
DEFAULT_DIR = Path("/var/lib/wattpost/keys")
PRIVATE_SEALED = "appliance.ed25519.sealed"
PUBLIC_RAW     = "appliance.ed25519.pub"
MACHINE_ANCHOR = "machine-anchor"

# Sealed file format (binary):
#   [1 byte version][24-byte nonce][N-byte ciphertext]
# version=1; bumped only if we ever change the AEAD primitive.
_FORMAT_VERSION = 1
_BOX_NONCE_SIZE = 24

# Domain-separation tag for the BLAKE2b key derivation so the
# machine-id can't be repurposed elsewhere with the same derived key.
_KDF_SALT = b"wattpost-id-v2-applkey-2026"


class KeypairError(Exception):
    """Raised when key generation, sealing, or unsealing fails."""


@dataclass(frozen=True)
class Keypair:
    """In-memory ed25519 keypair. The signing-key is sensitive — never
    log it, never marshal it. Public + fingerprint are safe to share."""
    signing_key: nacl.signing.SigningKey
    verify_key:  nacl.signing.VerifyKey
    fingerprint: str    # hex sha256(public)[:16]

    def public_key_b64(self) -> str:
        return base64.urlsafe_b64encode(bytes(self.verify_key)).decode("ascii")

    def sign(self, message: bytes) -> bytes:
        """Sign `message` and return the 64-byte raw signature."""
        return self.signing_key.sign(message).signature


def fingerprint_of(public_key: bytes) -> str:
    """Stable identifier for a public key. First 16 hex chars of
    SHA-256(public_key) — collision probability negligible at our
    fleet size, short enough for log lines and JWT claims."""
    return hashlib.sha256(public_key).hexdigest()[:16]


def _machine_anchor(dir_: Path) -> bytes:
    """Source of entropy for the SecretBox key.

    Preference order:
      1. PERSISTED anchor file under our own keys dir — wins whenever
         present. This is what survives Docker container recreates
         (the keys dir is bind-mounted; /etc/machine-id is NOT and
         gets re-minted by Docker on every recreate). Pi installs
         also benefit: /etc/machine-id is stable on Pi, but the
         persisted anchor still wins so the two storage modes share
         the same code path.
      2. /etc/machine-id — used to SEED the persisted anchor on
         first run when we have it (gives the anchor host-binding
         entropy beyond what secrets.token_bytes provides). Once
         persisted, we never re-read /etc/machine-id.
      3. /var/lib/dbus/machine-id — older systems, same role as #2.
      4. secrets.token_bytes — pure-random fallback. Used when no
         machine-id source is available; still persisted so it
         survives restarts.

    Disk-clone defence: cloning a host's disk WITHOUT the keys dir
    re-runs path #1's miss → path #2/#3/#4 → fresh anchor → can't
    decrypt the original sealed key → forced re-pair. Cloning WITH
    the keys dir gives the attacker decryptable keys — but that's
    no worse than today (the sealed key is also on the disk), and
    the proper hardware-bound defence ships in Phase 10.

    PRIOR BUG (pre-v0.1.94): preferred /etc/machine-id directly,
    which Docker re-mints on every container recreate. Result:
    every `docker compose pull && up -d` broke unsealing and forced
    a Phase 1 key rotation. Fixed by always preferring the
    bind-mount-surviving anchor file.
    """
    dir_.mkdir(parents=True, exist_ok=True)
    anchor_file = dir_ / MACHINE_ANCHOR

    # Path #1: persisted anchor wins whenever present + valid.
    try:
        if anchor_file.is_file():
            data = anchor_file.read_bytes().strip()
            if len(data) >= 16:
                return data
    except OSError:
        pass

    # First-run path: seed the persisted anchor from machine-id when
    # available, else from secrets.token_bytes.
    seed: bytes | None = None
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            raw = candidate.read_text(encoding="ascii").strip()
            if raw and len(raw) >= 16:
                seed = raw.encode("ascii")
                break
        except (OSError, UnicodeDecodeError):
            continue
    if seed is None:
        seed = secrets.token_bytes(32)

    try:
        anchor_file.write_bytes(seed)
        os.chmod(anchor_file, 0o600)
    except OSError as e:
        # If we can't persist, we can still use it for THIS process
        # lifetime — but any restart will mint a new key (and break
        # cloud pairing). Log loudly.
        log.warning("machine-anchor persist failed at %s: %s — keys "
                    "won't survive restart", anchor_file, e)
    return seed


def _derive_box_key(anchor: bytes) -> bytes:
    """Derive a 32-byte SecretBox key from the machine anchor.

    BLAKE2b in keyed mode with a static salt + 32-byte digest. Standard
    KDF shape — not as strong as Argon2 but the threat model is
    "attacker has the sealed file but doesn't have the anchor", not
    "attacker is brute-forcing offline" (anchor itself is 32+ bytes
    of entropy, so brute-force is infeasible).
    """
    h = hashlib.blake2b(digest_size=32, salt=_KDF_SALT[:16], person=b"applkey-2026")
    h.update(anchor)
    return h.digest()


def _seal(private_key_bytes: bytes, box_key: bytes) -> bytes:
    """Encrypt the raw private key with the derived box key + a fresh
    nonce. Returns the on-disk wire format."""
    nonce = secrets.token_bytes(_BOX_NONCE_SIZE)
    box = nacl.secret.SecretBox(box_key)
    ciphertext = box.encrypt(private_key_bytes, nonce).ciphertext
    # SecretBox.encrypt returns a EncryptedMessage that includes the
    # nonce by default; we strip and re-prepend ours so the wire format
    # stays explicit (version byte first, then nonce, then ct).
    return bytes([_FORMAT_VERSION]) + nonce + ciphertext


def _unseal(blob: bytes, box_key: bytes) -> bytes:
    """Reverse `_seal`. Raises KeypairError on any failure (bad
    version, truncated file, wrong key, tampered ciphertext)."""
    if len(blob) < 1 + _BOX_NONCE_SIZE + 16:
        raise KeypairError("sealed file too short")
    version = blob[0]
    if version != _FORMAT_VERSION:
        raise KeypairError(f"unsupported sealed format version {version}")
    nonce = blob[1:1 + _BOX_NONCE_SIZE]
    ciphertext = blob[1 + _BOX_NONCE_SIZE:]
    box = nacl.secret.SecretBox(box_key)
    try:
        return box.decrypt(ciphertext, nonce)
    except CryptoError as e:
        raise KeypairError(f"decrypt failed: {e}") from e


def load_or_create(dir_: Path | str = DEFAULT_DIR) -> Keypair:
    """Return the keypair for this appliance.

    First call generates + persists a new ed25519 keypair. Subsequent
    calls load the existing one from disk. Idempotent.

    Raises KeypairError if the existing file is present but won't
    decrypt — that means either the machine-id changed (disk moved to
    a new host) or the file is corrupted. Caller decides whether to
    fail-loud or regenerate (default: fail-loud; regenerating without
    a deliberate user action would silently invalidate every cloud-
    side trust relationship).
    """
    dir_ = Path(dir_)
    sealed_path = dir_ / PRIVATE_SEALED
    public_path = dir_ / PUBLIC_RAW
    anchor = _machine_anchor(dir_)
    box_key = _derive_box_key(anchor)

    if sealed_path.is_file():
        try:
            sealed_blob = sealed_path.read_bytes()
            raw_private = _unseal(sealed_blob, box_key)
            signing_key = nacl.signing.SigningKey(raw_private)
            verify_key = signing_key.verify_key
            fp = fingerprint_of(bytes(verify_key))
            log.info("appliance keypair loaded (fingerprint=%s)", fp)
            return Keypair(signing_key, verify_key, fp)
        except KeypairError as e:
            # Sealed file present but undecryptable. Pre-v0.1.94 this
            # would refuse to regenerate, leaving the appliance stuck
            # with no Phase 1 keypair (and no Phase 3 OIDC client).
            # Triggered by: Docker container recreate when the prior
            # seal used /etc/machine-id (which Docker re-mints).
            #
            # New behaviour: log loudly, delete the broken sealed file
            # + public copy, fall through to fresh generation. The
            # cloud's /upgrade endpoint handles this idempotently and
            # records it as a key rotation in audit (rotated_from_
            # fingerprint), so trust history isn't lost.
            log.warning(
                "appliance keypair decrypt failed: %s — auto-regenerating "
                "(prior key is gone, cloud will record this as a rotation "
                "on next /upgrade). Common cause: Docker container recreate "
                "with the pre-v0.1.94 anchor scheme.", e,
            )
            for stale in (sealed_path, public_path):
                try:
                    stale.unlink(missing_ok=True)
                except OSError as unlink_err:
                    log.warning("could not unlink stale keypair file %s: %s",
                                stale, unlink_err)

    # Fresh generation.
    log.info("generating new appliance ed25519 keypair")
    signing_key = nacl.signing.SigningKey.generate()
    verify_key = signing_key.verify_key
    raw_private = bytes(signing_key)
    raw_public  = bytes(verify_key)
    fp = fingerprint_of(raw_public)

    dir_.mkdir(parents=True, exist_ok=True)
    sealed_blob = _seal(raw_private, box_key)
    # Write atomically — rename is atomic on POSIX, so a crashed mid-
    # write doesn't leave a half-file the next boot can't read.
    tmp = sealed_path.with_suffix(sealed_path.suffix + ".tmp")
    tmp.write_bytes(sealed_blob)
    os.chmod(tmp, 0o600)
    tmp.replace(sealed_path)
    # Public key cached alongside as raw bytes for cheap reads
    # without unsealing.
    public_path.write_bytes(raw_public)
    os.chmod(public_path, 0o644)

    log.info("appliance keypair generated + persisted (fingerprint=%s)", fp)
    return Keypair(signing_key, verify_key, fp)


# Convenience helpers for callers that only need the public side
# (cloud upload, status endpoint) without unsealing the private.
def public_key_b64(dir_: Path | str = DEFAULT_DIR) -> str | None:
    p = Path(dir_) / PUBLIC_RAW
    if not p.is_file():
        return None
    try:
        return base64.urlsafe_b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        return None


def fingerprint(dir_: Path | str = DEFAULT_DIR) -> str | None:
    p = Path(dir_) / PUBLIC_RAW
    if not p.is_file():
        return None
    try:
        return fingerprint_of(p.read_bytes())
    except OSError:
        return None
