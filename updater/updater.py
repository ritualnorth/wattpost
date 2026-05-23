"""WattPost updater container — minimal Watchtower replacement.

Why this exists
---------------
`containrrr/watchtower` ships a Docker SDK Go client whose default API
version (1.25) is rejected by Docker engine 29+. Ubuntu 24.04 LTS ships
docker.io 29 by default, so a large slice of customer installs can't
use Watchtower today. We need cloud-driven Update-now to work on those
boxes, so we own the updater.

What it does
------------
One HTTP endpoint, one job. POST /v1/update with a Bearer token →
pull the wattpost image, recreate the container with the new image,
preserve all config + volumes.

We don't reinvent compose's recreate logic — we shell out to
`docker compose -f <file> pull && up -d <service>` against a bind-
mounted copy of the user's compose file. Compose handles the
container rename + rollover semantics correctly. We just trigger it.

API shape mirrors Watchtower's HTTP API so the appliance code path
(solar_monitor/cloud/service.py:236 dispatcher in #265) doesn't need
to change: same endpoint, same auth header.

Environment
-----------
  WATTPOST_UPDATER_TOKEN  (or WATCHTOWER_HTTP_API_TOKEN — fallback for
                           customers migrating from Watchtower compose)
  COMPOSE_FILE            path to the compose file inside this container
                          (default /host-compose/docker-compose.yml)
  SERVICE_NAME            service to pull+restart (default "wattpost")
  POLL_INTERVAL           auto-poll seconds (default 86400, 0 disables)
  PORT                    listen port (default 8080)
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT          = int(os.environ.get("PORT", "8080"))
TOKEN         = (os.environ.get("WATTPOST_UPDATER_TOKEN")
                 or os.environ.get("WATCHTOWER_HTTP_API_TOKEN")
                 or "").strip()
COMPOSE_FILE  = os.environ.get("COMPOSE_FILE", "/host-compose/docker-compose.yml")
SERVICE_NAME  = os.environ.get("SERVICE_NAME", "wattpost")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "86400"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("updater")

if not TOKEN:
    log.error("token required — set WATTPOST_UPDATER_TOKEN or "
              "WATCHTOWER_HTTP_API_TOKEN before starting")
    raise SystemExit(2)

# Serialise concurrent update triggers — auto-poll + an HTTP call
# can race. Lock means whichever fires first finishes; the other
# returns immediately.
_lock = threading.Lock()


def do_update() -> tuple[bool, str]:
    """Pull + restart the configured service via docker compose CLI.
    Returns (ok, message). Holds the global lock so concurrent
    triggers don't race."""
    if not _lock.acquire(blocking=False):
        log.info("update: already in progress, skipping")
        return False, "update already in progress"
    try:
        if not os.path.isfile(COMPOSE_FILE):
            msg = f"compose file not found at {COMPOSE_FILE} — bind-mount missing?"
            log.error("update: %s", msg)
            return False, msg
        log.info("update: pull %s service=%s", COMPOSE_FILE, SERVICE_NAME)
        pull = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "pull", SERVICE_NAME],
            capture_output=True, text=True, timeout=600,
        )
        if pull.returncode != 0:
            tail = (pull.stderr or pull.stdout)[-400:]
            log.error("update: pull failed rc=%d: %s", pull.returncode, tail)
            return False, f"pull failed: {tail}"
        log.info("update: up -d %s", SERVICE_NAME)
        up = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "up", "-d", SERVICE_NAME],
            capture_output=True, text=True, timeout=180,
        )
        if up.returncode != 0:
            tail = (up.stderr or up.stdout)[-400:]
            log.error("update: up failed rc=%d: %s", up.returncode, tail)
            return False, f"up failed: {tail}"
        log.info("update: done")
        return True, "ok"
    finally:
        _lock.release()


class Handler(BaseHTTPRequestHandler):
    def _ok_auth(self) -> bool:
        h = self.headers.get("Authorization", "")
        if not h.startswith("Bearer "):
            return False
        return h[len("Bearer "):].strip() == TOKEN

    def do_GET(self):  # noqa: N802
        # GET /healthz so the compose healthcheck can probe us without
        # the token. Doesn't leak anything sensitive.
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}\n')
            return
        self.send_response(404); self.end_headers()

    def do_POST(self):  # noqa: N802
        if self.path != "/v1/update":
            self.send_response(404); self.end_headers(); return
        if not self._ok_auth():
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("WWW-Authenticate", 'Bearer realm="wattpost-updater"')
            self.end_headers()
            self.wfile.write(b'{"error":"unauthorized"}\n')
            return
        # Fire-and-forget. The caller's container is about to be
        # restarted by us, so any awaited response would never arrive.
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"queued"}\n')
        threading.Thread(target=do_update, daemon=True).start()

    def log_message(self, format, *args):  # noqa: A002
        log.info("http %s %s", self.address_string(), format % args)


def auto_poll_loop() -> None:
    """Scheduled auto-poll. Same trigger as the HTTP endpoint —
    customers who want fully hands-off updates leave POLL_INTERVAL
    at the default 86400 (daily). Set to 0 to disable."""
    if POLL_INTERVAL <= 0:
        return
    log.info("auto-poll loop: every %ds", POLL_INTERVAL)
    while True:
        time.sleep(POLL_INTERVAL)
        log.info("auto-poll: trigger")
        do_update()


if __name__ == "__main__":
    if POLL_INTERVAL > 0:
        threading.Thread(target=auto_poll_loop, daemon=True).start()
    log.info("wattpost-updater listening :%d (compose=%s service=%s poll=%ds)",
             PORT, COMPOSE_FILE, SERVICE_NAME, POLL_INTERVAL)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
