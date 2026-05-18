#!/usr/bin/env python3
"""Capture short interaction videos from the live appliance dashboard
for the marketing landing page.

Drives http://192.168.1.100:8000 (Ritual North's dev appliance) via
playwright, records short clips in webm, drops them into
cloud/wattpost_cloud/web/static/img/.

Each clip is intentionally short (5-8 s) and silent so the landing
page can autoplay-muted-loop them without weighing the page down.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from playwright.async_api import async_playwright


APPLIANCE_BASE = "http://192.168.1.100:8000"
OUT_DIR = (Path(__file__).resolve().parent.parent
           / "cloud" / "wattpost_cloud" / "web" / "static" / "img")


# Each entry: (output filename, route, viewport, hold seconds, optional setup)
CLIPS = [
    # The hero — dashboard with the SoC donut + flow strip live.
    # Slightly oversized viewport so the chart fits without scrolling.
    ("video-dashboard.webm", "/",        {"width": 1280, "height": 800}, 7),
    # Device tab — shows the multi-vendor list with the silent badge.
    ("video-devices.webm",   "/#/devices", {"width": 1280, "height": 800}, 6),
    # Mobile dashboard — vertical orientation, hand-held framing.
    ("video-mobile.webm",    "/",        {"width": 414,  "height": 896}, 7),
    # Kiosk view — chrome-free, giant SoC donut + flow. 16:9 aspect
    # for a wall-mounted touch-display / monitor feel; the lock=1
    # query strips the Exit button so the recording stays clean.
    ("video-kiosk.webm",     "/kiosk?lock=1", {"width": 1280, "height": 720}, 8),
]


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for fname, route, viewport, hold_s in CLIPS:
            print(f"recording {fname} ({viewport['width']}x{viewport['height']})…")
            # Per-clip context so each gets its own video file.
            ctx = await browser.new_context(
                viewport=viewport,
                record_video_dir=str(OUT_DIR),
                record_video_size=viewport,
                color_scheme="dark",   # OS-level dark preference
            )
            page = await ctx.new_page()
            try:
                # Force the appliance's theme to dark BEFORE the page
                # loads — localStorage is read by an inline script in
                # <head> on first paint, so we seed it via init script.
                # See solar_monitor/web/index.html line ~36.
                await page.add_init_script(
                    "try { localStorage.setItem('wp-theme', 'dark'); } catch (_) {}"
                )
                # Don't use networkidle — SSE keeps the connection alive
                # forever, so playwright would wait the full 30 s timeout.
                # `domcontentloaded` returns as soon as initial HTML is
                # parsed; the hold_s sleep below covers the chart paint.
                await page.goto(APPLIANCE_BASE + route, wait_until="domcontentloaded")
                # Give the chart libraries + first SSE snapshot time to
                # land, then hold long enough that the live values tick.
                await asyncio.sleep(hold_s)
            finally:
                await page.close()
                await ctx.close()
                # playwright writes the recording to a random filename
                # in OUT_DIR; rename to the intended name.
                vids = sorted(
                    OUT_DIR.glob("*.webm"),
                    key=lambda p: p.stat().st_mtime, reverse=True,
                )
                # Find the freshest webm that isn't already one of our
                # intended outputs.
                intended = {c[0] for c in CLIPS}
                for v in vids:
                    if v.name not in intended:
                        dst = OUT_DIR / fname
                        if dst.exists():
                            dst.unlink()
                        shutil.move(str(v), str(dst))
                        size_kb = dst.stat().st_size // 1024
                        print(f"  → {dst.name} ({size_kb} KB)")
                        break
        await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
