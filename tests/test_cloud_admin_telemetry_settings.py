"""Settings -> Cloud: the telemetry switch is exposed, validated, persisted and
doesn't clobber other fields."""
import asyncio

import msgspec
import yaml

from solar_monitor.api import cloud_admin as ca
from solar_monitor.config import CloudCfg


class _Cfg:
    def __init__(self, cloud):
        self.cloud = cloud


def test_view_exposes_the_switch_with_safe_defaults():
    paired = _Cfg(CloudCfg(bearer_token="x", telemetry_sync=False, telemetry_minutes=9))
    v = ca.cloud_config_view(paired)
    assert v["telemetry_sync"] is False and v["telemetry_minutes"] == 9
    assert ca.cloud_config_view(_Cfg(None))["telemetry_sync"] is True


def _put(tmp_path, cloud, **payload):
    cfg = _Cfg(cloud)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"cloud": ca._serialize_cloud(cloud)}))
    state = {"config": cfg, "config_path": str(path)}
    data = msgspec.convert({"endpoint": cloud.endpoint, "heartbeat_minutes": cloud.heartbeat_minutes,
                            **payload}, ca.CloudConfigPayload)
    out = asyncio.run(ca.update_cloud_config.fn(data, state))
    return cfg, out, yaml.safe_load(path.read_text())["cloud"]


def test_switching_uploads_off_persists_live_and_needs_no_restart(tmp_path):
    cloud = CloudCfg(bearer_token="x", appliance_id=3, label="van")
    cfg, out, saved = _put(tmp_path, cloud, telemetry_sync=False)
    assert cfg.cloud.telemetry_sync is False and saved["telemetry_sync"] is False
    assert out["restart_required"] is False
    assert cfg.cloud.bearer_token == "x" and cfg.cloud.appliance_id == 3      # untouched


def test_changing_endpoint_still_asks_for_a_restart(tmp_path):
    cloud = CloudCfg(bearer_token="x")
    cfg, out, _ = _put(tmp_path, cloud, endpoint="https://other.example")
    assert out["restart_required"] is True


def test_cadence_is_validated(tmp_path):
    import pytest
    from litestar.exceptions import HTTPException
    for bad in (0, 61, -5):
        with pytest.raises(HTTPException):
            _put(tmp_path, CloudCfg(bearer_token="x"), telemetry_minutes=bad)
    cfg, _, saved = _put(tmp_path, CloudCfg(bearer_token="x"), telemetry_minutes=2)
    assert cfg.cloud.telemetry_minutes == 2 and saved["telemetry_minutes"] == 2
