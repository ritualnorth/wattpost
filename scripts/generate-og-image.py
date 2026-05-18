#!/usr/bin/env python3
"""Generate the social-unfurl OG image via headless Chromium.

Output: cloud/wattpost_cloud/web/static/img/og-landing.png (1200×630).

The card is laid out in HTML+CSS — much easier than fighting PIL
fonts. Playwright renders it at exact viewport size and screenshots.
Re-runnable; overwrites the existing PNG.

    .venv/bin/python scripts/generate-og-image.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


OUT = (Path(__file__).resolve().parent.parent
       / "cloud" / "wattpost_cloud" / "web" / "static" / "img" / "og-landing.png")


HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 1200px; height: 630px; overflow: hidden; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "Inter", "Helvetica Neue", Arial, sans-serif;
    background:
      radial-gradient(circle at 80% 20%, #1d3a5f 0%, transparent 50%),
      radial-gradient(circle at 20% 80%, #2d5a4f 0%, transparent 50%),
      #0a0d12;
    color: #e8edf3;
    padding: 70px 80px;
    position: relative;
  }
  .brand {
    display: flex; align-items: center; gap: 14px;
    font-size: 32px; font-weight: 700; letter-spacing: -0.01em;
  }
  .brand svg { color: #3a86ff; }
  h1 {
    font-size: 72px; font-weight: 800; line-height: 1.08;
    letter-spacing: -0.02em;
    margin-top: 60px;
    max-width: 900px;
  }
  .lede {
    margin-top: 28px;
    font-size: 28px; line-height: 1.45;
    color: #b8c2cc;
    max-width: 880px;
    font-weight: 400;
  }
  .stack {
    position: absolute; bottom: 70px; left: 80px;
    display: flex; gap: 36px;
    color: #8a94a3; font-size: 20px;
  }
  .stack b { color: #e8edf3; font-weight: 700; }
  .url {
    position: absolute; bottom: 70px; right: 80px;
    color: #5ec18e; font-size: 22px; font-weight: 600;
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
  }
  .accent { color: #5ec18e; }
</style></head>
<body>
  <div class="brand">
    <svg viewBox="0 0 24 24" width="42" height="42" fill="none"
         stroke="currentColor" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round">
      <rect x="2" y="3" width="20" height="12" rx="1"/>
      <line x1="7"  y1="15" x2="7"  y2="22"/>
      <line x1="17" y1="15" x2="17" y2="22"/>
      <path d="M13 5 L8 10 L11 10 L9 13 L15 8 L12 8 Z"
            fill="currentColor" stroke="none"/>
    </svg>
    <span>WattPost</span>
  </div>

  <h1>Off-grid solar.<br/>Three vendors. <span class="accent">One Pi.</span></h1>

  <p class="lede">
    Self-hosted dashboard that reads Renogy, Victron and JK BMS
    gear over Bluetooth. Local-first, MQTT for Home Assistant,
    optional cloud for remote access.
  </p>

  <div class="stack">
    <span><b>Renogy</b> · 5 drivers</span>
    <span><b>Victron</b> · 8 drivers</span>
    <span><b>JK BMS</b> · BLE</span>
  </div>

  <div class="url">wattpost.cloud</div>
</body></html>"""


async def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1200, "height": 630})
        page = await ctx.new_page()
        await page.set_content(HTML, wait_until="networkidle")
        await page.screenshot(path=str(OUT), full_page=False,
                              clip={"x": 0, "y": 0, "width": 1200, "height": 630})
        await browser.close()
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
