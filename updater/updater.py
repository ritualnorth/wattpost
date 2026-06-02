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
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT          = int(os.environ.get("PORT", "8080"))
TOKEN         = (os.environ.get("WATTPOST_UPDATER_TOKEN")
                 or os.environ.get("WATCHTOWER_HTTP_API_TOKEN")
                 or "").strip()
COMPOSE_FILE  = os.environ.get("COMPOSE_FILE", "/host-compose/docker-compose.yml")
SERVICE_NAME  = os.environ.get("SERVICE_NAME", "wattpost")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "86400"))
# Compose infers the project name from the directory containing the
# compose file. From inside the updater container that's "host-compose"
# (the bind-mount path), but on the host the user's stack was started
# from their actual directory (typically "wattpost"). Without an explicit
# project name, `compose up -d` thinks no containers exist, tries to
# create fresh ones, and collides on container names. Auto-detect from
# the running service's compose label; fall back to an env override;
# final fallback "wattpost" matches the convention in our docs.
PROJECT_NAME  = os.environ.get("COMPOSE_PROJECT_NAME", "").strip()

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


_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


def _detect_project_name() -> str:
    """Read the compose project name from the running service container's
    `com.docker.compose.project` label. If we can't reach docker or the
    container isn't running yet, fall back to the env override and then
    the "wattpost" convention. Cached after first successful read."""
    if PROJECT_NAME:
        return PROJECT_NAME
    try:
        out = subprocess.run(
            ["docker", "inspect", "--format",
             '{{index .Config.Labels "com.docker.compose.project"}}',
             SERVICE_NAME],
            capture_output=True, text=True, timeout=10,
        )
        name = (out.stdout or "").strip()
        if name:
            log.info("project: auto-detected %r from running %s container",
                     name, SERVICE_NAME)
            return name
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("project: docker inspect failed (%s) — falling back", e)
    log.info("project: defaulting to 'wattpost' (override via COMPOSE_PROJECT_NAME)")
    return "wattpost"


def _pin_tag_in_compose(version: str) -> tuple[bool, str]:
    """Rewrite the image: line of SERVICE_NAME in COMPOSE_FILE to use
    the given version tag, leaving repo + everything else alone.
    Bound under the same _lock as do_update() to prevent concurrent
    writers.

    Implementation: parse the file as text (not YAML) so comments
    and quirks survive. Find the SERVICE_NAME block, then the first
    `image:` line beneath it, then rewrite its tag. Re-runnable;
    idempotent if the tag's already pinned."""
    if not _VERSION_RE.match(version):
        return False, f"invalid version format: {version!r}"
    try:
        with open(COMPOSE_FILE, "r") as f:
            lines = f.readlines()
    except OSError as e:
        return False, f"can't read {COMPOSE_FILE}: {e}"

    svc_header_re = re.compile(rf"^\s+{re.escape(SERVICE_NAME)}:\s*$")
    in_service = False
    service_indent = None
    rewrote = False
    for i, ln in enumerate(lines):
        if svc_header_re.match(ln):
            in_service = True
            service_indent = len(ln) - len(ln.lstrip())
            continue
        if in_service:
            # Bail out if we hit a sibling service (same/lesser indent
            # AND ends with ":" — a new top-level under services).
            stripped = ln.rstrip("\n")
            if stripped and not stripped.startswith(" " * (service_indent + 2)):
                if stripped.startswith(" " * service_indent) and stripped.endswith(":"):
                    break
            m = re.match(r"^(\s+image:\s*)(\S+?)(\s*(?:#.*)?)$", ln)
            if m:
                head, ref, tail = m.group(1), m.group(2), m.group(3)
                # Strip any existing tag; default to ":latest" if there
                # was none. We replace EVERYTHING after the last colon
                # that's part of the tag, being careful of sha256: digests.
                if "@" in ref:
                    # digest — strip it; we're moving to a tag pin.
                    repo = ref.split("@", 1)[0].rsplit(":", 1)[0]
                elif ":" in ref:
                    repo = ref.rsplit(":", 1)[0]
                else:
                    repo = ref
                new_ref = f"{repo}:{version}"
                if new_ref == ref:
                    log.info("pin: already on %s, no rewrite needed", new_ref)
                    return True, "no-op"
                lines[i] = f"{head}{new_ref}{tail}\n" if not tail.endswith("\n") else f"{head}{new_ref}{tail}"
                log.info("pin: rewrote image %r → %r", ref, new_ref)
                rewrote = True
                break
    if not rewrote:
        return False, f"no image: line found under service {SERVICE_NAME!r}"
    try:
        with open(COMPOSE_FILE, "w") as f:
            f.writelines(lines)
    except OSError as e:
        return False, f"can't write {COMPOSE_FILE}: {e}"
    return True, "ok"


def do_update(version: str | None = None) -> tuple[bool, str]:
    """Pull + restart the configured service via docker compose CLI.
    If `version` is set, pin that image tag in the compose file first
    (used by #270 auto-rollback). Holds the global lock so concurrent
    triggers don't race."""
    if not _lock.acquire(blocking=False):
        log.info("update: already in progress, skipping")
        return False, "update already in progress"
    try:
        if not os.path.isfile(COMPOSE_FILE):
            msg = f"compose file not found at {COMPOSE_FILE} — bind-mount missing?"
            log.error("update: %s", msg)
            return False, msg
        if version:
            ok, msg = _pin_tag_in_compose(version)
            if not ok:
                log.error("update: tag pin failed: %s", msg)
                return False, f"tag pin failed: {msg}"
        project = _detect_project_name()
        log.info("update: pull %s project=%s service=%s%s", COMPOSE_FILE,
                 project, SERVICE_NAME, f" (pinned v{version})" if version else "")
        pull = subprocess.run(
            ["docker", "compose", "-p", project, "-f", COMPOSE_FILE,
             "pull", SERVICE_NAME],
            capture_output=True, text=True, timeout=600,
        )
        if pull.returncode != 0:
            tail = (pull.stderr or pull.stdout)[-400:]
            log.error("update: pull failed rc=%d: %s", pull.returncode, tail)
            return False, f"pull failed: {tail}"
        log.info("update: up -d %s", SERVICE_NAME)
        # `--no-deps` so we never recreate ourselves; `--pull never` because
        # we already pulled and don't want a stale-cache surprise.
        up = subprocess.run(
            ["docker", "compose", "-p", project, "-f", COMPOSE_FILE,
             "up", "-d", "--no-deps", "--pull", "never", SERVICE_NAME],
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
        parsed = urlparse(self.path)
        if parsed.path != "/v1/update":
            self.send_response(404); self.end_headers(); return
        if not self._ok_auth():
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("WWW-Authenticate", 'Bearer realm="wattpost-updater"')
            self.end_headers()
            self.wfile.write(b'{"error":"unauthorized"}\n')
            return
        # Optional ?version=X.Y.Z — pins that tag in the compose file
        # before pulling. Used by #270 cloud-orchestrated rollback;
        # daemon constructs the right image ref for our updater.
        qs = parse_qs(parsed.query or "")
        version = (qs.get("version") or [None])[0]
        # Fire-and-forget. The caller's container is about to be
        # restarted by us, so any awaited response would never arrive.
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"queued"}\n')
        threading.Thread(target=do_update, args=(version,), daemon=True).start()

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
