# Kiosk mode

Designed for an old tablet mounted on a wall: a chrome-free
full-viewport view showing **just the SoC donut and the power flow
strip**, big enough to read across a room.

## How to launch it

- Open `http://<your-pi>:8000/#/kiosk` in any browser on the tablet.
- Or tick **Settings → Appearance → Kiosk view on this device**.
  That sets a flag in `localStorage` for this browser/device only —
  next refresh lands straight in the kiosk view, while your phone
  and laptop keep the normal UI.

A small fade-out **exit** icon sits in the top-right corner.

## What's on screen

- **Donut** — SoC + rotating pulse (green when charging, amber when
  discharging) + a small charge-rate pill (`+64 W`).
- **Flow strip** — Solar / Battery / Load tiles connected by a slim
  gradient line. The order *is* the direction.

Connector arrowheads and pills are dropped on purpose — at couch
distance they read as visual noise. The tile order and colours do
the storytelling.

## Wall-mount tips

- Use a stand or a 3D-printed VESA bracket for the tablet.
- Put the browser in **fullscreen** mode (F11 on a laptop browser,
  Add to Home Screen for the PWA experience on iOS / Android).
- WattPost requests a **Wake Lock** when the kiosk route is active,
  so the screen shouldn't dim out while it's visible. Re-acquired
  automatically when the tab regains focus.
- If you also want the URL hidden on iOS, install the PWA: Safari →
  Share → Add to Home Screen. Launching from that icon runs without
  the URL bar.
