"""CloudCfg must survive every save path unchanged.

Regression: Settings-save and re-pair rebuilt CloudCfg by copying fields one
by one, so any field added later (sso_secret in 0.0.38, the telemetry
settings in this change) was silently reset on the next save. The
serialiser and the edit/pair handlers now work from the whole struct.
"""
import msgspec
import yaml

from solar_monitor.api.cloud_admin import _serialize_cloud
from solar_monitor.config import CloudCfg


def _full() -> CloudCfg:
    return CloudCfg(
        endpoint="https://example.test", bearer_token="tok", appliance_id=7,
        label="van", heartbeat_minutes=3, telemetry_sync=False,
        telemetry_minutes=9, telemetry_backfill_hours=2,
        tunnel_token="t", tunnel_hostname="h", sso_secret="s", kiosk_token="k",
    )


def test_every_field_round_trips_through_yaml():
    c = _full()
    text = yaml.safe_dump({"cloud": _serialize_cloud(c)})
    back = msgspec.convert(yaml.safe_load(text)["cloud"], CloudCfg)
    assert back == c


def test_defaults_are_not_written_but_core_fields_are():
    out = _serialize_cloud(CloudCfg(bearer_token="x"))
    assert set(out) == {"endpoint", "bearer_token", "appliance_id", "label", "heartbeat_minutes"}


def test_replace_keeps_untouched_fields():
    """The shape of the edit and re-pair handlers."""
    c = _full()
    edited = msgspec.structs.replace(c, endpoint="https://other.test", heartbeat_minutes=10)
    assert edited.telemetry_minutes == 9 and edited.telemetry_sync is False
    assert edited.sso_secret == "s" and edited.kiosk_token == "k"


def test_new_cloudcfg_fields_are_automatically_serialised():
    """Guard against someone adding a CloudCfg field and a hand-written list again."""
    names = {f for f in CloudCfg.__struct_fields__}
    c = msgspec.structs.replace(CloudCfg(), **{
        f: (not getattr(CloudCfg(), f) if isinstance(getattr(CloudCfg(), f), bool)
            else "changed" if isinstance(getattr(CloudCfg(), f), str)
            else 42)
        for f in names if f != "appliance_id"})
    out = _serialize_cloud(c)
    assert names - {"appliance_id"} <= set(out)
