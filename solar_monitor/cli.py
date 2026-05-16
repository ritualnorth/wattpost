"""solar-monitor CLI.

Two subcommands:
  poll  — run one poll cycle and print JSON (scripting, CI, debugging)
  serve — run the daemon: scheduler + Litestar API + web UI
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

import uvicorn

from .api import build_app
from .config import load_config
from .diagnostics import install as install_log_ring
from .orchestrator import poll_once


def cmd_poll(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    result = asyncio.run(poll_once(config))
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0 if not result.get("errors") else 1


def cmd_serve(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s %(name)s: %(message)s",
    )
    # NOTE: install_log_ring() is called from the Litestar on_startup
    # hook, AFTER uvicorn finishes reconfiguring logging — if we attach
    # here uvicorn's dictConfig wipes the handler before any of our
    # daemon code emits log lines.
    config = load_config(args.config)
    # First-boot password generation. The Pi SD-card image's install.sh
    # used to do this, but Docker installs never ran install.sh and so
    # shipped with no password — combined with the auth middleware's
    # "no password = bypass" rule that left the dashboard wide open to
    # anyone who could reach it (incl. via the cloud tunnel). Idempotent:
    # once the hash file exists this is a no-op.
    from . import web_auth as _wa
    _wa.ensure_first_boot_password()
    app = build_app(
        config=config,
        db_path=args.db,
        interval_seconds=args.interval,
        config_path=args.config,
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level.lower(),
        access_log=False,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="solar-monitor")
    parser.add_argument("--log-level", default="INFO")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_poll = sub.add_parser("poll", help="Run a single poll cycle, print JSON")
    p_poll.add_argument("--config", required=True)
    p_poll.set_defaults(func=cmd_poll)

    p_serve = sub.add_parser("serve", help="Run the daemon: scheduler + API + web UI")
    p_serve.add_argument("--config", required=True)
    p_serve.add_argument("--db", default="solar-monitor.db")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--interval", type=int, default=60, help="Poll interval (seconds)")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
