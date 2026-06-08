"""Client for the privileged helper daemon (wattpost-helperd, #33).

The unprivileged `wattpost` daemon used to reach root via `sudo <helper>`.
sudo is setuid-root and forces NoNewPrivileges off in our systemd unit,
which blocked the rest of the sandbox. Instead the daemon now sends a tiny
JSON request over a group-restricted Unix socket to a small root service
that performs a fixed allow-list of operations. This module is that client.

It is intentionally dependency-free (stdlib sockets) and never raises: a
missing/!connectable socket reports unavailable so callers degrade to the
legacy path (during migration) or no-op (Docker / dev), exactly like the
old sudo wrapper did.
"""
from __future__ import annotations

import json
import logging
import os
import socket

log = logging.getLogger(__name__)

SOCKET_PATH = "/run/wattpost/helper.sock"
_TIMEOUT = 25.0


def is_available() -> bool:
    """True only where the helper socket exists (the Pi image with the
    helper installed). False on Docker / dev so callers fall back."""
    return os.path.exists(SOCKET_PATH)


def call(action: str, **args) -> dict:
    """Send one request, return the helper's response dict. On any transport
    failure returns {ok:False, err:...} rather than raising."""
    if not is_available():
        return {"ok": False, "out": "", "err": "helper unavailable"}
    req = json.dumps({"action": action, "args": args}) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(_TIMEOUT)
            s.connect(SOCKET_PATH)
            s.sendall(req.encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        resp = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        if not isinstance(resp, dict):
            return {"ok": False, "out": "", "err": "bad helper response"}
        return resp
    except Exception as e:
        log.warning("helper call %s failed: %s", action, e)
        return {"ok": False, "out": "", "err": str(e)}
