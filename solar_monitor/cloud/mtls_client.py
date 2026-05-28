"""Appliance-side mTLS client cert lifecycle (Identity v2 Phase 6B).

What this does:

  1. After identity-v2 /upgrade succeeds, post to
     /api/internal/identity/v2/mtls/issue to get a leaf cert signed
     by the cloud CA, binding the appliance's existing ed25519
     public key to its identity.
  2. Persist three files alongside the keypair under
     /var/lib/wattpost/keys/:
       * appliance_cert.pem      (leaf cert from cloud)
       * appliance_cert_key.pem  (ed25519 private key in PKCS#8)
       * cloud_ca_chain.pem      (CA chain to verify cloud's TLS)
  3. Detect upcoming expiry and re-issue when <30 days remain.

What this does NOT do (yet, Phase 6B-B):

  * Switch heartbeats / internal API calls to use the mTLS client.
    That requires a coordinated change: Caddy listener configured
    for `client_auth optional`, cloud middleware that trusts the
    Caddy-injected verified-DN header. Until those land the cert
    sits ready but unused; the v1 bearer path stays authoritative.

We deliberately avoid taking a dep on `cryptography` (heavy native
package). PyNaCl already gives us the raw ed25519 seed bytes, and
the PKCS#8 v1 encoding for ed25519 is a fixed 16-byte ASN.1 prefix
followed by the raw seed, small enough to construct by hand. The
leaf cert (PEM) is returned by the cloud and stored as-is; we never
need to parse it on the appliance.
"""
from __future__ import annotations

import base64
import datetime
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..auth import keypair as _keypair

log = logging.getLogger(__name__)


# Same keys dir as the rest of Identity v2 (#303, #305 etc).
_KEY_DIR = Path(os.environ.get("WATTPOST_KEYS_DIR") or "/var/lib/wattpost/keys")

CERT_PATH = _KEY_DIR / "appliance_cert.pem"
KEY_PATH  = _KEY_DIR / "appliance_cert_key.pem"
CA_PATH   = _KEY_DIR / "cloud_ca_chain.pem"
META_PATH = _KEY_DIR / "appliance_cert_meta.json"

# Renew when <30 days remain on the leaf. Cloud mints with a
# LEAF_LIFETIME_DAYS lifetime (set in cloud.mtls); 30 days of
# headroom matches Let's Encrypt's customary renewal interval and
# avoids edge cases on offline appliances that boot only once a
# week.
RENEW_WHEN_REMAINING_DAYS = 30


# ---------------------------------------------------------------- #
#  PKCS#8 v1 unencrypted ed25519 private key (fixed ASN.1 prefix)   #
# ---------------------------------------------------------------- #


# Hand-built PKCS#8 v1 wrapper for an ed25519 32-byte seed. RFC 8410
# §7 specifies this exact byte layout: SEQUENCE { version 0,
# algorithm OID 1.3.101.112, privateKey OCTET STRING containing
# OCTET STRING { 32 raw bytes } }. Total length 48 bytes binary.
# Hard-coded so we don't need `cryptography` on the appliance.
_PKCS8_ED25519_PREFIX = bytes.fromhex(
    "302e020100300506032b657004220420"
)


def _ed25519_private_pem(seed_bytes: bytes) -> str:
    if len(seed_bytes) != 32:
        raise ValueError("ed25519 seed must be exactly 32 bytes")
    der = _PKCS8_ED25519_PREFIX + seed_bytes
    b64 = base64.b64encode(der).decode("ascii")
    # PEM wraps at 64 chars per line by convention.
    lines = "\n".join(b64[i:i+64] for i in range(0, len(b64), 64))
    return f"-----BEGIN PRIVATE KEY-----\n{lines}\n-----END PRIVATE KEY-----\n"


# ---------------------------------------------------------------- #
#  Persistence                                                      #
# ---------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CertMeta:
    serial:      str
    fingerprint: str
    not_after:   str  # ISO-8601 UTC


def _atomic_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _persist_materials(*, cert_pem: str, key_pem: str, ca_pem: str,
                       meta: CertMeta) -> None:
    _atomic_write(CERT_PATH, cert_pem, mode=0o644)
    _atomic_write(KEY_PATH,  key_pem,  mode=0o600)
    _atomic_write(CA_PATH,   ca_pem,   mode=0o644)
    _atomic_write(META_PATH, json.dumps({
        "serial":      meta.serial,
        "fingerprint": meta.fingerprint,
        "not_after":   meta.not_after,
    }, indent=2) + "\n", mode=0o600)


def load_meta() -> CertMeta | None:
    """Read the persisted cert metadata. Returns None when no cert
    has been issued yet."""
    try:
        body = json.loads(META_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    try:
        return CertMeta(
            serial=str(body["serial"]),
            fingerprint=str(body["fingerprint"]),
            not_after=str(body["not_after"]),
        )
    except (KeyError, TypeError):
        return None


def cert_paths() -> tuple[Path, Path, Path] | None:
    """Return (cert, key, ca) paths if all three files are present
    AND not expired. Used by future Phase 6B-B heartbeat client. Use
    `is_ready()` if you just want a boolean."""
    if not (CERT_PATH.is_file() and KEY_PATH.is_file() and CA_PATH.is_file()):
        return None
    meta = load_meta()
    if meta is None:
        return None
    try:
        not_after = datetime.datetime.fromisoformat(meta.not_after)
        if not_after.tzinfo is None:
            not_after = not_after.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    if not_after <= datetime.datetime.now(datetime.timezone.utc):
        return None
    return CERT_PATH, KEY_PATH, CA_PATH


def is_ready() -> bool:
    return cert_paths() is not None


def needs_renewal() -> bool:
    """True if no cert exists, OR <RENEW_WHEN_REMAINING_DAYS remain.
    Cheap; only reads the meta JSON."""
    meta = load_meta()
    if meta is None:
        return True
    try:
        not_after = datetime.datetime.fromisoformat(meta.not_after)
        if not_after.tzinfo is None:
            not_after = not_after.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return True
    remaining = not_after - datetime.datetime.now(datetime.timezone.utc)
    return remaining.days < RENEW_WHEN_REMAINING_DAYS


# ---------------------------------------------------------------- #
#  Issuance                                                         #
# ---------------------------------------------------------------- #


_PEM_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
    re.DOTALL,
)


def _looks_like_pem_chain(pem: str) -> bool:
    """Loose validation that the response body looks like one or
    more PEM certs. We don't parse, that's the responsibility of
    the eventual TLS stack."""
    return bool(_PEM_RE.search(pem or ""))


async def ensure_cert(*, endpoint: str, bearer_token: str) -> bool:
    """If a fresh cert exists, return True without contacting cloud.
    Otherwise POST to /api/internal/identity/v2/mtls/issue, persist
    the response materials, and return True. Returns False on any
    network / cloud-side failure (a Phase 1 keypair must exist,
    callers should run this AFTER /upgrade succeeds)."""
    if not needs_renewal():
        return True

    try:
        kp = _keypair.load_or_create()
    except Exception:
        log.exception("mtls: keypair load failed, cannot request cert")
        return False

    url = f"{endpoint.rstrip('/')}/api/internal/identity/v2/mtls/issue"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type":  "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.post(url, json={}, headers=headers)
    except Exception as e:
        log.warning("mtls: /issue request failed: %s, will retry next boot", e)
        return False
    if r.status_code == 404:
        log.debug("mtls: cloud endpoint 404, older deploy, will skip")
        return False
    if r.status_code >= 400:
        log.warning("mtls: /issue HTTP %s: %s", r.status_code, r.text[:200])
        return False

    try:
        body = r.json()
    except Exception:
        log.warning("mtls: /issue returned non-JSON body")
        return False

    cert_pem = body.get("cert_pem")
    ca_pem   = body.get("ca_chain_pem")
    serial   = body.get("serial")
    fp       = body.get("fingerprint")
    not_after = body.get("not_after")
    if not all([cert_pem, ca_pem, serial, fp, not_after]):
        log.warning("mtls: /issue response missing required fields")
        return False
    if not _looks_like_pem_chain(cert_pem) or not _looks_like_pem_chain(ca_pem):
        log.warning("mtls: /issue returned non-PEM body, refusing to persist")
        return False

    # Re-derive PKCS#8 from the SAME ed25519 seed our keypair has.
    # If somebody rotates the keypair, the existing cert is silently
    # invalidated (the cloud has a different pubkey on file now); a
    # subsequent ensure_cert() call will rebuild materials with the
    # rotated key.
    seed = bytes(kp.signing_key)[:32]
    key_pem = _ed25519_private_pem(seed)

    _persist_materials(
        cert_pem=cert_pem,
        key_pem=key_pem,
        ca_pem=ca_pem,
        meta=CertMeta(serial=str(serial), fingerprint=str(fp),
                      not_after=str(not_after)),
    )
    log.info(
        "mtls: persisted leaf cert serial=%s fp=%s not_after=%s",
        serial, str(fp)[:16], not_after,
    )
    return True
