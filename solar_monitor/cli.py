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


_DEFAULT_DB_ARG = "solar-monitor.db"


def _resolve_db_path(args: argparse.Namespace, config) -> str:
    """Pick the DB path. Order of precedence:

      1. --db on the command line, but only if the operator passed
         something other than the default. Lets a power-user override.
      2. config.db_path if set in config.yaml. This is the canonical
         path for both Pi and Docker installs (mapped to a persistent
         volume on Docker).
      3. The CLI default, ./solar-monitor.db — backward-compat for
         anyone still launching without either.

    Critical bug this fixes: before v0.0.60 the daemon ALWAYS used
    args.db (default "./solar-monitor.db"), so on Docker the SQLite
    file lived in /app inside the container's ephemeral writable
    layer. Every `docker compose pull && up -d` swap of the image
    wiped /app and took every metric the user had ever collected
    with it. config.db_path was settable but completely ignored.

    Also handles MIGRATION of a legacy /app/solar-monitor.db to the
    new persistent location on first startup of v0.0.60+, so users
    don't lose their CURRENT data when the fix lands. Copy + leave
    .legacy.bak alongside so the old file is preserved for one
    container restart in case anything goes wrong.
    """
    cli_explicit = args.db != _DEFAULT_DB_ARG
    cfg_path = getattr(config, "db_path", None)
    if cli_explicit:
        chosen = args.db
    elif cfg_path:
        chosen = cfg_path
    else:
        chosen = args.db
    # One-shot migration from the legacy in-image-layer location.
    from pathlib import Path
    target = Path(chosen)
    legacy = Path.cwd() / _DEFAULT_DB_ARG
    if (not target.exists()
            and legacy.exists() and legacy != target
            and legacy.stat().st_size > 0):
        import shutil
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, target)
            legacy.rename(legacy.with_suffix(legacy.suffix + ".legacy.bak"))
            logging.getLogger("solar_monitor.cli").warning(
                "DB persistence fix: copied legacy %s → %s "
                "(left .legacy.bak alongside as a one-cycle safety net)",
                legacy, target,
            )
        except OSError as e:
            logging.getLogger("solar_monitor.cli").error(
                "DB migration failed (%s → %s): %s. "
                "Falling back to opening at the configured path; "
                "any data in the legacy location is unreachable.",
                legacy, target, e,
            )
    return chosen


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
    db_path = _resolve_db_path(args, config)
    app = build_app(
        config=config,
        db_path=db_path,
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
    p_serve.add_argument("--db", default=_DEFAULT_DB_ARG)
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--interval", type=int, default=60, help="Poll interval (seconds)")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
