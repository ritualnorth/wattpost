# Kiosk view

Kiosk is the **chrome-free, read-only** version of the dashboard — a giant SoC donut + power-flow visualisation, no menus, no settings buttons. Designed for permanent wall-mount or fridge-magnet displays.

## URLs

| Where you are | URL |
| - | - |
| LAN (same network as Pi) | `http://wattpost.local/kiosk` |
| Through the cloud tunnel | `https://<slug>.wattpost.io/kiosk` |

The `/kiosk` route is **always anonymous** — never gated by the local web password, even when one is set. So an old tablet stuck to the wall can boot, open the bookmark, and live there.

## Auto-launching on a device

- **Old Android tablet:** install **Fully Kiosk Browser** (free), set start URL to the kiosk URL, enable "stay on" and "lock screen orientation". You get a dedicated solar display for ~$0 of new hardware.
- **iPad:** Safari → bookmark → Add to Home Screen → tap the icon → fullscreen. Set Auto-Lock to Never in Settings.
- **Raspberry Pi connected to a monitor:** install chromium + xdotool, drop a systemd unit that launches `chromium --kiosk <url>` on boot.
- **Browser on a laptop:** the dashboard's Settings → **Kiosk by default on this device** toggle in localStorage. Opens kiosk on first load every time.

## Copying a kiosk link from the cloud

On `wattpost.cloud`, every paired appliance card has a **Kiosk link** button. Click it → copies `https://<slug>.wattpost.io/kiosk` to your clipboard. Paste into the tablet's browser.

## What kiosk shows

- State of charge as a donut (big — visible across a room)
- Real-time power flow: solar → bank → load with arrows + watt readings
- Net watts indicator
- Auto-refreshing every poll (~60 s)

Nothing else. No tabs, no menus, no settings. If you need history, alerts, or device config, that's the full dashboard.

## Hiding the exit button

The kiosk view has a small "Exit kiosk" button in the corner so you can get back to the full UI from the kiosk device itself. If you want to hide it from prying fingers, add `?lock=1` to the URL: `https://<slug>.wattpost.io/kiosk?lock=1`.

## Going back to the full dashboard

Click the small "Exit" arrow in the kiosk's corner, or just navigate to `/` instead of `/kiosk`. If you set "Kiosk by default" in Settings, clear the localStorage key `wp-kiosk-default` (or use the toggle to switch off).
