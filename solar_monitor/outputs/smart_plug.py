"""Smart-plug output adapters (#163 followup).

Two off-grid-friendly target protocols, both local-HTTP, no broker,
no cloud, no Home Assistant in the loop:

  - **Shelly Gen2** (Plug S, Plus, Pro). Local JSON-RPC at
    http://<host>/rpc/Switch.Set?id=0&on=<bool>. State read via
    Switch.GetStatus. Auth via basic-auth header if the user has
    set a device password.

  - **Tasmota** (any Sonoff / Athom / similar flashed with Tasmota).
    Local command surface at http://<host>/cm?cmnd=Power%20<On|Off>.
    State read from the same endpoint with cmnd=Power.

Both share the same WattPost contract: each smart_plug entry in
config.yaml becomes one ControllableOutput. OutputsService
registers them alongside Modbus-discovered outputs; the existing
toggle() and apply_snapshot() paths drive them through the same
solar-pause + manual-toggle code as a Renogy load relay.

The Settings UI populates the solar-pause dropdown from the
shared /api/outputs list, so any registered plug shows up as a
selectable target.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from .base import ControllableOutput, WriteResult


log = logging.getLogger(__name__)


# Per-plug HTTP timeout. Smart plugs on a local LAN normally answer
# in well under 500 ms; we give the request 3 s of headroom because
# a Wi-Fi plug that's just come out of sleep can take longer to
# respond to its first packet. Beyond 3 s a "flip the relay" command
# is no longer useful anyway.
_HTTP_TIMEOUT_S = 3.0


def _http_get(url: str, auth: str | None) -> dict[str, Any]:
    """Blocking HTTP GET → JSON. Runs in a thread; never call from
    the event loop directly. Returns the parsed JSON body, raises
    on non-2xx or non-JSON. Plain dict so the call sites stay async-
    transport-agnostic."""
    req = urllib.request.Request(url)
    if auth:
        req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
        body = resp.read()
        try:
            return json.loads(body or b"{}")
        except json.JSONDecodeError:
            # Tasmota's text endpoints also work; surface raw text
            # in a stable wrapper so callers don't need separate
            # branches.
            return {"_raw": body.decode("utf-8", errors="replace")}


async def _http_get_async(url: str, *, auth: str | None) -> dict[str, Any]:
    return await asyncio.get_event_loop().run_in_executor(
        None, _http_get, url, auth,
    )


def _basic_auth_header(user: str | None, password: str | None) -> str | None:
    """Build the value side of an Authorization: Basic header. Shelly
    Gen2 wants the user portion empty when only a password is set;
    Tasmota allows either. We accept either form and synthesise
    `<user>:<password>` accordingly."""
    if not password:
        return None
    raw = f"{user or ''}:{password}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------- Shelly

class ShellyGen2Adapter:
    """Shelly Gen2 / Plus / Pro switch over local JSON-RPC.

    Device docs: https://shelly-api-docs.shelly.cloud/gen2/
    We talk to the first switch channel (id=0); multi-channel
    Shellys would add a per-channel suffix to the output id but
    nobody using the Plug S range needs that.
    """
    vendor = "smart_plug"
    handles_kinds = ("smart_plug",)

    def __init__(self, host: str, name: str,
                 user: str | None = None, password: str | None = None) -> None:
        self.host = host
        self.name = name
        self._auth = _basic_auth_header(user, password)

    # Smart plugs aren't device-snapshot-driven so the protocol's
    # `discover(device)` doesn't apply; OutputsService walks them
    # directly via config. We still expose discover() for protocol
    # conformance — always returns []. The output is registered
    # via build_output() instead.
    def discover(self, device: dict[str, Any]) -> list[ControllableOutput]:
        return []

    def build_output(self, plug_id: str) -> ControllableOutput:
        return ControllableOutput(
            id=plug_id,
            device_label=plug_id,
            name=self.name,
            kind="smart_plug",
            capabilities=("toggle",),
        )

    async def write(
        self, output: ControllableOutput, on: bool,
        *, transport=None, slave_id: int = 0,
    ) -> WriteResult:
        url = (
            f"http://{self.host}/rpc/Switch.Set?id=0&on="
            f"{'true' if on else 'false'}"
        )
        try:
            await _http_get_async(url, auth=self._auth)
        except Exception as e:
            return WriteResult(
                ok=False, confirmed_state=None,
                detail=f"shelly write failed: {type(e).__name__}: {e}",
            )
        # Shelly's Set returns the new state but our pollers re-read
        # truth from the device anyway; do an explicit read-back so
        # the WriteResult carries confirmed_state without waiting
        # for the next poll tick.
        state = await self.read_state()
        return WriteResult(ok=(state == (1 if on else 0)),
                           confirmed_state=state,
                           detail=None if state is not None else "read-back returned no state")

    async def read_state(self) -> int | None:
        try:
            data = await _http_get_async(
                f"http://{self.host}/rpc/Switch.GetStatus?id=0",
                auth=self._auth,
            )
        except Exception as e:
            log.info("shelly %s: read_state failed: %s", self.host, e)
            return None
        out = data.get("output")
        if isinstance(out, bool):
            return 1 if out else 0
        return None

    def read_state_from_snapshot(
        self, output: ControllableOutput, snapshot: dict[str, Any],
    ) -> int | None:
        # Smart plugs are not part of the device-poll snapshot. The
        # OutputsService.apply_snapshot path calls read_state() on
        # the adapter directly for plugs (see service.py changes).
        return None


# ---------------------------------------------------------------- Tasmota

class TasmotaAdapter:
    """Tasmota-flashed plug over /cm?cmnd=Power... .

    Tasmota's HTTP surface is the simplest of any smart-plug
    protocol: GET /cm?cmnd=Power%20<state>. Auth is via the same
    `user=admin&password=...` query params (Tasmota also accepts
    Basic; we use query for compatibility with stock builds that
    haven't enabled the auth web header)."""
    vendor = "smart_plug"
    handles_kinds = ("smart_plug",)

    def __init__(self, host: str, name: str,
                 user: str | None = None, password: str | None = None) -> None:
        self.host = host
        self.name = name
        self.user = user
        self.password = password

    def discover(self, device: dict[str, Any]) -> list[ControllableOutput]:
        return []

    def build_output(self, plug_id: str) -> ControllableOutput:
        return ControllableOutput(
            id=plug_id,
            device_label=plug_id,
            name=self.name,
            kind="smart_plug",
            capabilities=("toggle",),
        )

    def _url(self, cmnd: str) -> str:
        params = {"cmnd": cmnd}
        if self.password:
            params["user"] = self.user or "admin"
            params["password"] = self.password
        return f"http://{self.host}/cm?{urllib.parse.urlencode(params)}"

    async def write(
        self, output: ControllableOutput, on: bool,
        *, transport=None, slave_id: int = 0,
    ) -> WriteResult:
        try:
            data = await _http_get_async(
                self._url(f"Power {'On' if on else 'Off'}"), auth=None,
            )
        except Exception as e:
            return WriteResult(
                ok=False, confirmed_state=None,
                detail=f"tasmota write failed: {type(e).__name__}: {e}",
            )
        # Tasmota returns {"POWER": "ON"} or {"POWER": "OFF"} when
        # the command succeeds. Treat absence as a fault.
        state_str = (data.get("POWER") or data.get("POWER1") or "").upper()
        if not state_str:
            return WriteResult(
                ok=False, confirmed_state=None,
                detail=f"tasmota response missing POWER field: {data!r}",
            )
        confirmed = 1 if state_str == "ON" else 0
        return WriteResult(
            ok=(confirmed == (1 if on else 0)),
            confirmed_state=confirmed,
        )

    async def read_state(self) -> int | None:
        try:
            data = await _http_get_async(self._url("Power"), auth=None)
        except Exception as e:
            log.info("tasmota %s: read_state failed: %s", self.host, e)
            return None
        state_str = (data.get("POWER") or data.get("POWER1") or "").upper()
        if state_str == "ON":
            return 1
        if state_str == "OFF":
            return 0
        return None

    def read_state_from_snapshot(
        self, output: ControllableOutput, snapshot: dict[str, Any],
    ) -> int | None:
        return None


def build_adapter(cfg: dict[str, Any]) -> ShellyGen2Adapter | TasmotaAdapter:
    """Factory keyed on the `kind` field in a smart_plug config entry.
    Raises ValueError on unknown kinds so a typo'd config doesn't
    silently no-op at runtime."""
    kind = (cfg.get("kind") or "").lower()
    name = cfg.get("name") or cfg.get("host") or "Smart plug"
    host = cfg["host"]
    user = cfg.get("user")
    password = cfg.get("password")
    if kind == "shelly_gen2":
        return ShellyGen2Adapter(host=host, name=name, user=user, password=password)
    if kind == "tasmota":
        return TasmotaAdapter(host=host, name=name, user=user, password=password)
    raise ValueError(f"unknown smart_plug kind: {kind!r}")
