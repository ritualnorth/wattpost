"""Regression tests for the aggregated integrations view (#18).

Settings → Integrations used to fire four parallel config requests and
fail the whole panel on a single 429. The fix folds them into one
/api/system/integrations response built from four pure config→dict
views. These lock in the two guarantees that matter:

  (a) the aggregate always carries all four sections, and
  (b) secrets stay masked — the same promise each individual endpoint
      made before the refactor.

Built on SimpleNamespace stubs so we exercise the view functions
without standing up Litestar or a real Config.
"""
from types import SimpleNamespace

from solar_monitor.api.forecast_admin import forecast_config_view
from solar_monitor.api.weather_admin import weather_config_view
from solar_monitor.api.cloud_admin import cloud_config_view
from solar_monitor.api.exporters_admin import mqtt_config_view


def _aggregate(config):
    # Mirrors api.app.get_integrations without the HTTP layer.
    return {
        "forecast": forecast_config_view(config),
        "weather":  weather_config_view(config),
        "cloud":    cloud_config_view(config),
        "mqtt":     mqtt_config_view(config),
    }


def test_aggregate_has_all_four_sections_when_unconfigured():
    cfg = SimpleNamespace(forecast=None, weather=None, cloud=None, exporters=[])
    agg = _aggregate(cfg)
    assert set(agg) == {"forecast", "weather", "cloud", "mqtt"}
    assert agg["forecast"]["configured"] is False
    assert agg["weather"]["configured"] is False
    assert agg["cloud"]["configured"] is False
    assert agg["mqtt"]["enabled"] is False


def test_secrets_are_masked_and_never_leak():
    fc = SimpleNamespace(
        provider="solcast", api_key="super-secret-key", resource_id="abc",
        lat=1.0, lon=2.0, array_kw=1.0, tilt_deg=30.0, azimuth_deg=0.0,
        system_efficiency=0.8, poll_hours=3,
    )
    cloud = SimpleNamespace(
        bearer_token="tok-secret", endpoint="https://wattpost.cloud",
        heartbeat_minutes=5, appliance_id="ap1", label="Site",
        tunnel_token="tt", tunnel_hostname="x.wattpost.cloud",
    )
    cfg = SimpleNamespace(forecast=fc, weather=None, cloud=cloud, exporters=[])
    agg = _aggregate(cfg)

    assert agg["forecast"]["configured"] is True
    assert agg["forecast"]["api_key"] == "****"
    assert agg["cloud"]["configured"] is True
    assert agg["cloud"]["bearer_token"] == "****"
    # Belt-and-braces: no raw secret should appear anywhere in the payload.
    blob = repr(agg)
    assert "super-secret-key" not in blob
    assert "tok-secret" not in blob


if __name__ == "__main__":
    test_aggregate_has_all_four_sections_when_unconfigured()
    test_secrets_are_masked_and_never_leak()
    print("ALL INTEGRATIONS-AGGREGATE TESTS PASS")
