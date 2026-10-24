"""Drive the real heartbeat_once dispatch path with a fake cloud reply."""
import asyncio
from types import SimpleNamespace


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


def test_unknown_kid_commands_cause_at_most_one_forced_jwks_refetch(monkeypatch):
    from solar_monitor.cloud import command_verify as cv
    env = Env()
    try:
        forced = []
        orig = cv.oidc_rp.fetch_jwks

        async def counting(url, force=False):
            if force:
                forced.append(1)
            return await orig(url, force=force)

        cv.oidc_rp.fetch_jwks = counting
        cmds = []
        for i in range(10):
            c = env.command(cmd_id=100 + i)
            c["signing_kid"] = f"made-up-{i}"
            cmds.append(c)
        s, dispatched = make_service(monkeypatch, {"commands": cmds}, 7)
        asyncio.run(beat(s))
        assert dispatched == []
        assert len(forced) == 1
        # and not again within the next minute, whatever the next heartbeat says
        asyncio.run(beat(s))
        assert len(forced) == 1
    finally:
        env.restore()


def test_command_list_is_capped_per_heartbeat(monkeypatch):
    env = Env()
    try:
        cmds = [env.command(cmd_id=200 + i) for i in range(svc.MAX_COMMANDS_PER_HEARTBEAT + 15)]
        s, dispatched = make_service(monkeypatch, {"commands": cmds}, 7)
        asyncio.run(beat(s))
        assert len(dispatched) == svc.MAX_COMMANDS_PER_HEARTBEAT
        assert dispatched == [200 + i for i in range(svc.MAX_COMMANDS_PER_HEARTBEAT)]
    finally:
        env.restore()


def test_non_dict_commands_are_skipped_not_fatal(monkeypatch):
    env = Env()
    try:
        s, dispatched = make_service(
            monkeypatch, {"commands": ["x", 5, None, env.command(cmd_id=300)]}, 7)
        asyncio.run(beat(s))
        assert dispatched == [300]
    finally:
        env.restore()


def test_loops_survive_unpair_clearing_the_cloud_config(monkeypatch):
    """/api/cloud/unpair sets config.cloud = None while both background loops run."""
    from solar_monitor.config import Config

    async def go():
        cfg = Config(transports=[], devices=[])
        cfg.cloud = CloudCfg(endpoint="https://cloud.example", bearer_token="t",
                             appliance_id=7, heartbeat_minutes=1)
        s = CloudService(cfg, SimpleNamespace(store=None))
        cfg.cloud = None                                  # unpaired
        # every accessor the loops use must tolerate it
        assert s._cfg_get("endpoint", "") == ""
        assert await s.heartbeat_once() is False
        # run the heartbeat loop; make its first sleep return immediately, then stop
        calls = []

        async def fast_wait_for(aw, timeout):
            calls.append(timeout)
            aw.close() if hasattr(aw, "close") else None
            if len(calls) >= 3:
                s._stop.set()
                return True
            raise asyncio.TimeoutError

        monkeypatch.setattr(svc.asyncio, "wait_for", fast_wait_for)
        await s._loop()
        assert len(calls) >= 3                            # kept idling, no AttributeError

    asyncio.run(go())


def test_telemetry_sync_callables_tolerate_unpair(monkeypatch):
    from solar_monitor.config import Config

    async def go():
        cfg = Config(transports=[], devices=[])
        cfg.cloud = CloudCfg(endpoint="https://cloud.example", bearer_token="t", appliance_id=7)
        s = CloudService(cfg, SimpleNamespace(store=None))

        async def noop():
            return None

        monkeypatch.setattr(s, "_identity_v2_upgrade_bg", noop)
        monkeypatch.setattr(s, "_loop", noop)
        await s.start()
        try:
            assert s._sync._enabled() is True
            cfg.cloud = None                              # /api/cloud/unpair
            assert s._sync._enabled() is False            # idles instead of raising
            assert s._sync._endpoint() == "" and s._sync._bearer() == ""
            assert s._sync._backfill_hours() > 0
            await asyncio.sleep(0.05)
            assert not s._sync_task.done()                # loop still alive
        finally:
            await s.stop()
    asyncio.run(go())
