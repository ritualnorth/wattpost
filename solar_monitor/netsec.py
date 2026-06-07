"""Host network security control (cloud #15, Phase B).

A thin, defensive wrapper around the root-owned ``wattpost-netctl`` helper,
invoked through a locked-down sudoers rule. The daemon runs as the non-root
``wattpost`` user; this is the only path by which it touches sshd or the
inbound firewall, and it can pass only fixed on/off switches.

On any host without the helper (dev shells, Docker installs, a fresh
checkout) every call is a safe no-op that reports ``supported=False`` — the
API and the boot reconcile never raise.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

HELPER = "/usr/local/sbin/wattpost-netctl"


def is_supported() -> bool:
    """True only where we can actually drive the host: the helper exists and
    sudo is present (the Pi image). False on Docker / dev so callers no-op."""
    return Path(HELPER).exists() and shutil.which("sudo") is not None


def _run(args: list[str], timeout: float = 15.0) -> tuple[bool, str]:
    if not is_supported():
        return False, "unsupported"
    try:
        r = subprocess.run(
            ["sudo", "-n", HELPER, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (r.stdout or "").strip()
        if r.returncode != 0:
            err = (r.stderr or out or "failed").strip()
            log.warning("netsec %s failed (rc=%d): %s", args, r.returncode, err)
            return False, err
        return True, out
    except Exception as e:  # subprocess timeout, OSError, …
        log.warning("netsec %s errored: %s", args, e)
        return False, str(e)


def _parse_status(out: str) -> dict:
    d: dict = {"supported": True, "ssh": None, "firewall": None}
    for tok in out.split():
        if tok.startswith("ssh="):
            d["ssh"] = tok[4:] == "on"
        elif tok.startswith("firewall="):
            d["firewall"] = tok[len("firewall="):] == "on"
    return d


def status() -> dict:
    """Live host state: {supported, ssh, firewall}. ssh/firewall are None
    when unknown/unsupported."""
    if not is_supported():
        return {"supported": False, "ssh": None, "firewall": None}
    ok, out = _run(["status"])
    return _parse_status(out) if ok else {"supported": True, "ssh": None, "firewall": None}


def apply(firewall_enabled: bool, ssh_enabled: bool) -> tuple[bool, str]:
    """Reconcile sshd + the inbound firewall to the requested state."""
    return _run([
        "apply",
        "on" if firewall_enabled else "off",
        "on" if ssh_enabled else "off",
    ])


def reconcile(web_cfg) -> None:
    """Best-effort: bring the host to match config on daemon boot. Never
    raises — a firewall/ssh hiccup must not stop the daemon starting."""
    if not is_supported():
        return
    fw = bool(getattr(web_cfg, "firewall_enabled", True)) if web_cfg is not None else True
    ssh = bool(getattr(web_cfg, "ssh_enabled", False)) if web_cfg is not None else False
    ok, out = apply(fw, ssh)
    log.info("netsec reconcile: firewall=%s ssh=%s -> %s",
             fw, ssh, out if ok else "FAILED")
