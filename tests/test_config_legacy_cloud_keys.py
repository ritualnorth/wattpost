"""Configs written before the tunnel was removed still carry
cloud.tunnel_token / tunnel_hostname / sso_secret. They must load without
error, be dropped, and never be written back."""
import yaml

from solar_monitor.api.cloud_admin import _serialize_cloud
from solar_monitor.config import load_config

_LEGACY = ("tunnel_token", "tunnel_hostname", "sso_secret")

_YAML = """
transports: []
devices: []
cloud:
  endpoint: https://wattpost.cloud
  bearer_token: tok
  appliance_id: 7
  label: van
  tunnel_token: secret-tunnel
  tunnel_hostname: van.example.test
  sso_secret: abcd
  kiosk_token: kk
"""


def test_legacy_cloud_keys_load_and_are_scrubbed(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_YAML)
    cfg = load_config(p)
    assert cfg.cloud is not None
    assert cfg.cloud.bearer_token == "tok" and cfg.cloud.appliance_id == 7
    for k in _LEGACY:
        assert not hasattr(cfg.cloud, k)
    # Not retained on disk, so no later read-modify-write can carry them.
    on_disk = yaml.safe_load(p.read_text())["cloud"]
    for k in _LEGACY:
        assert k not in on_disk
    assert on_disk["bearer_token"] == "tok" and on_disk["label"] == "van"
    text = p.read_text()
    assert "secret-tunnel" not in text and "abcd" not in text


def test_legacy_keys_not_serialised(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_YAML)
    cfg = load_config(p)
    out = _serialize_cloud(cfg.cloud)
    for k in _LEGACY:
        assert k not in out


def test_config_without_legacy_keys_is_untouched(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("transports: []\ndevices: []\ncloud:\n  bearer_token: tok\n")
    before = p.read_text()
    load_config(p)
    assert yaml.safe_load(p.read_text())["cloud"]["bearer_token"] == "tok"
    assert before.count("bearer_token") == 1
