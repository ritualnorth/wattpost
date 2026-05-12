"""HTTP API + web UI.

Single Litestar app: REST endpoints under /api/* and the static SvelteKit
build under /. For now `/` serves a hand-written index.html that proves the
chart UX before we commit to the SvelteKit toolchain.
"""
from .app import build_app

__all__ = ["build_app"]
