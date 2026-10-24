"""Runs the node:test suite in tests/js (chart window, range stats, forecast
interpolation, unit labels). Skipped where node is not installed; the GitHub
runner has it."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_chart_logic():
    r = subprocess.run(
        ["node", "--test", "tests/js/charts.test.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
