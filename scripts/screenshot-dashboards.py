#!/usr/bin/env python3
"""Capture marketing screenshots of the dashboards via headless Chromium.

Run from the repo root with the local venv:

    .venv/bin/python scripts/screenshot-dashboards.py

Drops PNGs into cloud/wattpost_cloud/web/static/img/ so the landing
page can <img src="/static/img/dashboard.png" /> them.

We capture both the live local appliance dashboard (full of real data
from this dev box's BLE polls) and demo.wattpost.io once it's up.

Re-runnable. Overwrites existing PNGs. No persistence — fresh chromium
each run so the dashboards never see stale localStorage.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT_DIR = Path(__file__).resolve().parent.parent / "cloud" / "wattpost_cloud" / "web" / "static" / "img"

# Each entry: (output filename, URL, viewport (w,h), wait selector or seconds)
SHOTS = [
    # Local appliance dashboard, desktop wide — the hero screenshot.
    ("dashboard-desktop.png", "http://localhost:8000/",
        {"width": 1440, "height": 900}, 5),
    # Same, mobile-ish viewport.
    ("dashboard-mobile.png",  "http://localhost:8000/",
        {"width": 414,  "height": 896}, 5),
    # Public demo (when live)
    ("demo-desktop.png",      "https://demo.wattpost.io/",
        {"width": 1440, "height": 900}, 6),
]


async def capture(page, url, viewport, wait_secs, out_path):
    await page.set_viewport_size(viewport)
    try:
        # `domcontentloaded`, NOT `networkidle` — the dashboards have
        # SSE / heartbeat polling that never lets the network settle.
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"  [skip] {url}: {e}", file=sys.stderr)
        return False
    # Give the page time to fetch its initial data + render charts.
    # The dashboards take ~3-5s for the first poll snapshot to come in
    # and the donut + flow viz to animate to their resting state.
    await page.wait_for_timeout(wait_secs * 1000)
    await page.screenshot(path=str(out_path), full_page=False)
    return True


async def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(device_scale_factor=2)  # @2x retina
        page = await ctx.new_page()
        ok = 0
        for filename, url, vp, wait_secs in SHOTS:
            print(f"==> {filename}  ({url}, {vp['width']}×{vp['height']})")
            out = OUT_DIR / filename
            success = await capture(page, url, vp, wait_secs, out)
            if success:
                kb = out.stat().st_size // 1024
                print(f"    saved {kb} KB → {out.relative_to(Path.cwd())}")
                ok += 1
        await browser.close()
        print(f"\n{ok}/{len(SHOTS)} screenshots captured.")
        return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
