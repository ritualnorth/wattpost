"""Drive the real heartbeat_once dispatch path with a fake cloud reply."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from solar_monitor.cloud import service as svc
from solar_monitor.cloud.service import CloudService
from solar_monitor.config import CloudCfg

from test_command_verify import Env


class FakeResp:
    status_code = 200
    text = ""

    def __init__(self, body):
        self._b = body

    def json(self):
        return self._b


def make_service(monkeypatch, reply, appliance_id):
    cfg = CloudCfg(endpoint="https://cloud.example", bearer_token="t", appliance_id=appliance_id)
    s = CloudService(cfg, SimpleNamespace(store=None))
    dispatched = []

    async def build():
        return {}

    async def dispatch(cmd):
        dispatched.append(cmd["id"])

    monkeypatch.setattr(s, "_build_payload", build)
    monkeypatch.setattr(s, "_signed_headers", lambda b: {})
    monkeypatch.setattr(s, "_dispatch_command", dispatch)
    monkeypatch.setattr(s, "_ledger", lambda: SimpleNamespace(seen=lambda i: False))

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return FakeResp(reply)

    monkeypatch.setattr(svc.httpx, "AsyncClient", Client)
    return s, dispatched


async def beat(s):
    ok = await s.heartbeat_once()
    await asyncio.sleep(0)
    for t in list(s._bg_tasks):
        await t
    return ok


def test_command_signed_for_another_appliance_is_not_dispatched(monkeypatch):
    env = Env()
    try:
        other = env.command(cmd_id=1, appliance_id=8)
        mine = env.command(cmd_id=2, appliance_id=7)
        s, dispatched = make_service(monkeypatch, {"commands": [other, mine]}, 7)
        assert asyncio.run(beat(s)) is True
        assert dispatched == [2]
    finally:
        env.restore()


def test_command_cannot_relabel_its_appliance_id(monkeypatch):
    """Rewriting the unsigned appliance_id field to match doesn't help: the
    signature covers the id this appliance verifies with."""
    env = Env()
    try:
        c = env.command(cmd_id=3, appliance_id=8)
        c["appliance_id"] = 7
        s, dispatched = make_service(monkeypatch, {"commands": [c]}, 7)
        asyncio.run(beat(s))
        assert dispatched == []
    finally:
        env.restore()


def test_unpaired_appliance_refuses_every_command(monkeypatch):
    env = Env()
    try:
        c = env.command(cmd_id=4, appliance_id=7)
        s, dispatched = make_service(monkeypatch, {"commands": [c]}, None)
        asyncio.run(beat(s))
        assert dispatched == []
    finally:
        env.restore()
