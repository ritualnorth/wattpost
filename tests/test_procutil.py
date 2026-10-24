"""Restart-in-place must work however the daemon was launched."""
import os
import sys

from solar_monitor import procutil


def _capture(monkeypatch, orig_argv):
    seen = {}
    monkeypatch.setattr(sys, "orig_argv", orig_argv, raising=False)
    monkeypatch.setattr(os, "execv", lambda path, argv: seen.update(path=path, argv=argv))
    procutil.reexec()
    return seen


def test_dash_m_launch_reruns_the_module_not_its_file(monkeypatch):
    # `python -m solar_monitor.cli serve`: sys.argv[0] would be the module's .py
    # path, and running that file as a script dies on its relative imports.
    seen = _capture(monkeypatch, ["python", "-m", "solar_monitor.cli", "serve", "--port", "8000"])
    assert seen["path"] == sys.executable
    assert seen["argv"] == [sys.executable, "-m", "solar_monitor.cli", "serve", "--port", "8000"]


def test_console_script_launch_keeps_its_script_and_args(monkeypatch):
    seen = _capture(monkeypatch, ["/opt/wattpost/venv/bin/python", "/opt/wattpost/venv/bin/solar-monitor",
                                  "--log-level", "INFO", "serve", "--config", "/etc/wattpost/config.yaml"])
    assert seen["argv"] == [sys.executable, "/opt/wattpost/venv/bin/solar-monitor",
                            "--log-level", "INFO", "serve", "--config", "/etc/wattpost/config.yaml"]


def test_interpreter_flags_survive(monkeypatch):
    seen = _capture(monkeypatch, ["python", "-X", "dev", "-m", "solar_monitor.cli", "serve"])
    assert seen["argv"][1:4] == ["-X", "dev", "-m"]
