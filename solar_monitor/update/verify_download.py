"""Verify a freshly-downloaded release against its signed manifest.

Called by `wattpost-update` (via `python -m solar_monitor.update.verify_download
<manifest.json> <tarball_sha256>`) with the CURRENTLY-RUNNING, trusted venv —
so the existing code authenticates the new download before it's staged/swapped.

Extracted from a shell heredoc so the grandfather-vs-abort decision (the part
where a bug would brick or wave through an update) is a unit-tested module.

Exit codes (the shell keys off these):
    0   verified — signature valid, sha matches, not a downgrade
    7   GRANDFATHER (proceed, warn): unsigned release, this box has no pinned
        key yet (pre-signing version), bad CLI usage, or ANY unexpected error.
        Fail-safe — an inconclusive result must never block an update.
    8   manifest sha256 != downloaded tarball  -> tamper, ABORT
    9   signature does not verify               -> tamper, ABORT
    10  validly-signed but a downgrade          -> ABORT (use wattpost-rollback)
"""
from __future__ import annotations

import json
import sys

# Exit-code constants (also handy for tests).
OK = 0
GRANDFATHER = 7
SHA_MISMATCH = 8
BAD_SIGNATURE = 9
DOWNGRADE = 10


def _is_downgrade(new: str, cur: str) -> bool:
    """True iff release `new` is strictly older than the running `cur`.
    Unparseable versions return False — never block on a version we can't read."""
    def parts(v: str) -> tuple:
        return tuple(
            (0, int(p)) if p.isdigit() else (1, p)
            for p in v.lstrip("v").split(".")
        )
    try:
        return parts(new) < parts(cur)
    except Exception:
        return False


def verify_download(manifest_path: str, tar_sha: str) -> int:
    """Return one of the exit-code constants above. Never raises."""
    try:
        from solar_monitor import __version__ as CUR
        from solar_monitor.update.release_verify import (
            verify_release, pinned_pubkey,
        )
        with open(manifest_path, encoding="utf-8") as f:
            m = json.load(f)
        sig = (m.get("signature") or "").strip()
        # A box still on a pre-signing version has no trust anchor, so it must
        # grandfather — otherwise every existing box would refuse the very
        # update that first ships the key.
        if pinned_pubkey() is None:
            return GRANDFATHER
        if not sig:
            return GRANDFATHER
        if m.get("sha256") != tar_sha:
            return SHA_MISMATCH
        if not verify_release(
            {"version": m["version"], "sha256": m["sha256"],
             "channel": m["channel"]}, sig,
        ):
            return BAD_SIGNATURE
        if _is_downgrade(str(m.get("version", "")), str(CUR)):
            return DOWNGRADE
        return OK
    except Exception:
        # Any unexpected error (missing module on old code, malformed JSON,
        # IO) grandfathers — this verification can never brick an update.
        return GRANDFATHER


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        return GRANDFATHER  # misuse -> never block an update
    return verify_download(argv[0], argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
