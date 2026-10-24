"""Process helpers."""
from __future__ import annotations

import os
import sys


def reexec() -> None:
    """Replace this process with a fresh copy of itself (restart in place).

    Re-runs the interpreter with the arguments it was *originally* started
    with (`sys.orig_argv`), so it works however it was launched: the
    `solar-monitor` console script, `python -m solar_monitor.cli ...`, or
    `python path/to/script.py`. Rebuilding the command line from `sys.argv`
    is wrong for `-m`: argv[0] is then the module's file path, and running that
    file as a script fails on its relative imports.
    """
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
    os.execv(sys.executable, [sys.executable, *argv[1:]])
