# Kiosk view

Kiosk is the **chrome-free, read-only** version of the dashboard. A giant SoC donut + power-flow visualisation, no menus, no settings buttons. Designed for permanent wall-mount or fridge-magnet displays.

## URLs

| Where you are | URL |
| - | - |
| LAN (same network as Pi) | `http://wattpost.local/kiosk` |

The `/kiosk` route is **always anonymous**. Never gated by the local web password, even when one is set. So an old tablet stuck to the wall can boot, open the bookmark, and live there.

## Auto-launching on a device

- **Old Android tablet:** install **Fully Kiosk Browser** (free), set start URL to the kiosk URL, enable "stay on" and "lock screen orientation". You get a dedicated solar display for ~$0 of new hardware.
- **iPad:** Safari → bookmark → Add to Home Screen → tap the icon → fullscreen. Set Auto-Lock to Never in Settings.
- **Raspberry Pi connected to a monitor:** install chromium + xdotool, drop a systemd unit that launches `chromium --kiosk <url>` on boot.
- **Browser on a laptop:** the dashboard's **Settings → Display → Open the local display in kiosk** switch. Opens kiosk on first load every time.

## Sharing a kiosk link from the cloud

On `wattpost.cloud`, open the site's **Manage** sheet and use **Wall display link**: give it a label, choose how long it lasts (a day up to no expiry), optionally pick a look (Halo, Ember or Command), and optionally lock it to the first device that opens it. The link is shown once, so copy it then and paste it into the tablet's browser.

The cloud kiosk is read-only and shows the live basics only: state of charge, power flow and today's totals. It never shows history, device details or your location, and it can't change anything. You can see how often each link has been opened and from which country, and revoke it from the same sheet, which stops it straight away.

## What kiosk shows

- State of charge as a donut (big. Visible across a room)
- Real-time power flow: solar → bank → load with arrows + watt readings
- Net watts indicator
- Auto-refreshing every poll (~60 s)

Nothing else. No tabs, no menus, no settings. If you need history, alerts, or device config, that's the full dashboard.

## Going back to the full dashboard

Click the small "Exit" arrow in the kiosk's corner, or just navigate to `/` instead of `/kiosk`. If you set "Kiosk by default" in Settings, clear the localStorage key `wp-kiosk-default` (or use the toggle to switch off).
