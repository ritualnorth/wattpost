"""Unit tests for the Prometheus exporter (#14): metric rendering and
the exporter's latest-result cache.
"""
import asyncio

from solar_monitor.export.prometheus import (
    PrometheusExporter,
    render_openmetrics,
)


def test_empty_result_still_renders_liveness():
    out = render_openmetrics(None)
    assert "# TYPE wattpost_up gauge" in out
    assert "wattpost_up 1" in out
    assert out.endswith("\n")


def test_numeric_device_metrics_become_labelled_gauges():
    result = {
        "timestamp": 1000.0,
        "devices": {
            "battery_0": {
                "soc_pct": 25.2,
                "voltage": 13.1,
                "online": True,        # bool -> 1
                "label": "battery_0",  # string -> skipped (not a metric)
                "note": None,          # None  -> skipped
            },
        },
    }
    out = render_openmetrics(result, now=1005.0)
    assert 'wattpost_soc_pct{device="battery_0"} 25.2' in out
    assert 'wattpost_voltage{device="battery_0"} 13.1' in out
    assert 'wattpost_online{device="battery_0"} 1.0' in out
    # Non-numeric values must not leak as metrics.
    assert "wattpost_label" not in out
    assert "wattpost_note" not in out
    # Each metric carries exactly one TYPE line.
    assert out.count("# TYPE wattpost_soc_pct gauge") == 1
    # Staleness derived from timestamp vs scrape time.
    assert "wattpost_poll_age_seconds 5.0" in out


def test_metric_names_and_labels_are_sanitised():
    result = {"devices": {'odd"name': {"cell.1.mv": 3300}}}
    out = render_openmetrics(result)
    # '.' in the key -> '_'
    assert "wattpost_cell_1_mv" in out
    # '"' in the device label is escaped, not raw.
    assert r'device="odd\"name"' in out


def test_exporter_caches_latest_and_renders():
    exp = PrometheusExporter(metric_prefix="wp")
    asyncio.run(exp.start())
    asyncio.run(exp.export({"devices": {"d": {"v": 1}}}))
    asyncio.run(exp.export({"devices": {"d": {"v": 2}}}))  # latest wins
    rendered = exp.render()
    assert 'wp_v{device="d"} 2.0' in rendered
    assert 'wp_v{device="d"} 1.0' not in rendered
    asyncio.run(exp.stop())


if __name__ == "__main__":
    test_empty_result_still_renders_liveness()
    test_numeric_device_metrics_become_labelled_gauges()
    test_metric_names_and_labels_are_sanitised()
    test_exporter_caches_latest_and_renders()
    print("ALL PROMETHEUS EXPORTER TESTS PASS")
