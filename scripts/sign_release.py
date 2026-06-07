#!/usr/bin/env python3
"""Sign a release manifest with the WattPost release key (Phase C auto-apply).

Runs in the release pipeline. The private key NEVER lives in this repo or on an
appliance — pass it via $WATTPOST_RELEASE_SIGNING_KEY (base64 of the raw 32-byte
Ed25519 seed) or --key-file. The signature this prints goes alongside the
release tarball; the appliance verifies it with the pinned public key (see
solar_monitor/update/release_verify.py).

One-time setup:
    python scripts/sign_release.py --genkey
  -> prints seed_b64 (store as a CI secret / offline — NEVER commit) and
     pubkey_b64 (pin it: set RELEASE_PUBKEY_B64 in release_verify.py, or ship
     it as /etc/wattpost/release-pubkey).

Per release:
    WATTPOST_RELEASE_SIGNING_KEY=<seed_b64> \
      python scripts/sign_release.py --version v0.1.200 --sha256 <hex> --channel stable
  -> prints the base64 signature (stdout) and the canonical manifest (stderr).

The (version, sha256, channel) here MUST match what wattpost-update fetches +
hashes, or verification fails closed and the box won't apply.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys

import nacl.signing


def _b64encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64decode(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _canonical(manifest: dict) -> bytes:
    return json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Sign a WattPost release manifest.")
    ap.add_argument("--genkey", action="store_true",
                    help="generate a keypair and exit (prints seed + pubkey)")
    ap.add_argument("--version", help="release tag, e.g. v0.1.200")
    ap.add_argument("--sha256", help="hex SHA256 of the release tarball")
    ap.add_argument("--channel", default="stable", help="stable|beta")
    ap.add_argument("--key-file", help="file with the base64 seed (alt to env)")
    args = ap.parse_args()

    if args.genkey:
        sk = nacl.signing.SigningKey.generate()
        sys.stderr.write(
            "Store the SEED as a secret (CI/offline) — NEVER commit it.\n"
            "Pin the PUBLIC key in the image (RELEASE_PUBKEY_B64 or "
            "/etc/wattpost/release-pubkey).\n"
        )
        print("seed_b64="   + _b64encode(bytes(sk)))
        print("pubkey_b64=" + _b64encode(bytes(sk.verify_key)))
        return 0

    if not (args.version and args.sha256):
        ap.error("--version and --sha256 are required (or use --genkey)")

    seed = os.environ.get("WATTPOST_RELEASE_SIGNING_KEY")
    if args.key_file:
        seed = open(args.key_file, encoding="utf-8").read().strip()
    if not seed:
        sys.exit("no signing key: set $WATTPOST_RELEASE_SIGNING_KEY or --key-file")

    sk = nacl.signing.SigningKey(_b64decode(seed))
    manifest = {"version": args.version, "sha256": args.sha256,
                "channel": args.channel}
    sig = sk.sign(_canonical(manifest)).signature
    sys.stderr.write("manifest: " + _canonical(manifest).decode("utf-8") + "\n")
    print(_b64encode(sig))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
