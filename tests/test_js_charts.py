"""Runs the node:test suite in tests/js (chart window, range stats, forecast
interpolation, unit labels). Skipped locally where node is not installed; in CI (CI=true)
missing node is a failure so the tests can't be skipped silently."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_js_chart_logic():
    if shutil.which("node") is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI so the chart tests cannot be skipped silently")
        pytest.skip("node not installed")
    r = subprocess.run(
        ["node", "--test", "tests/js/charts.test.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
