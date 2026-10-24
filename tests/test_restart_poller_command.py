"""restart_poller: stop then start the scheduler in-process, report status."""
import asyncio
from types import SimpleNamespace

import pytest

from solar_monitor.cloud.service import CloudService
from solar_monitor.config import CloudCfg


@pytest.fixture(autouse=True)
def _private_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WATTPOST_DB", str(tmp_path / "db.sqlite"))


def make(monkeypatch, scheduler):
    s = CloudService(CloudCfg(endpoint="https://c.example", bearer_token="t", appliance_id=1), scheduler)
    statuses = []

    async def patch(cmd_id, status, **kw):
        statuses.append((status, kw.get("error")))

    monkeypatch.setattr(s, "_patch_command_status", patch)
    return s, statuses


class Sched:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def stop(self):
        self.calls.append("stop")

    async def start(self):
        self.calls.append("start")
        if self.fail:
            raise RuntimeError("no adapter")


def test_restart_poller_stops_then_starts(monkeypatch):
    sched = Sched()
    s, statuses = make(monkeypatch, sched)
    asyncio.run(s._dispatch_command({"id": 9, "kind": "restart_poller"}))
    assert sched.calls == ["stop", "start"]
    assert [x[0] for x in statuses] == ["picked_up", "applying", "success"]


def test_restart_poller_failure_is_reported(monkeypatch):
    s, statuses = make(monkeypatch, Sched(fail=True))
    asyncio.run(s._dispatch_command({"id": 10, "kind": "restart_poller"}))
    assert statuses[-1][0] == "failed" and "no adapter" in statuses[-1][1]
