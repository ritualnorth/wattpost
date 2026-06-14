# Wall-display kiosk (HDMI / Pi touchscreen) — #8

Drive a physical display wired to the appliance — a wall-mounted HDMI panel
or the official Raspberry Pi touchscreen — straight into the dashboard's
fullscreen kiosk view, with no keyboard, no desktop, no browser chrome.

## How it works

The Pi OS image is **Lite** (no desktop / display server / browser). So the
feature adds a minimal kiosk stack on demand:

- **`cage`** — a single-client Wayland kiosk compositor (one app, fullscreen,
  no window management).
- **`chromium`** — the browser. Chromium (not a lighter WPE browser) because
  the dashboard SPA uses canvas charts + Leaflet maps that render reliably in
  Chromium.
- **`seatd`** — hands `cage` the DRM/KMS seat (GPU + input) without a login
  session.

`wattpost-kiosk-display` (launcher) reads `kiosk.display_enabled` from
`config.yaml`, waits for the local dashboard, then runs
`cage -- chromium --kiosk http://localhost/kiosk[/<mode>]`. The
`wattpost-kiosk-display.service` unit runs it at boot.

**The unit is enabled on every image but self-gates:** it exits 0 immediately
when `display_enabled` is off OR no panel is attached (`/dev/dri/card*`
absent) OR chromium isn't installed — so a headless box is completely
unaffected and systemd never restarts it.

## Config (`kiosk:` block in config.yaml)

```yaml
kiosk:
  display_enabled: true     # drive an attached panel (default: false)
  display_mode: ""          # ""|home|van|cabin|marine|kiosk -> /kiosk[/<mode>]
  display_rotate: 0         # 0|90|180|270 for portrait / wall mounts
  skin: halo                # halo|ember|command (the existing kiosk skin)
```

## Enabling on a real box (opt-in — installs the heavy stack)

The launcher + unit ship on every image, but chromium (~hundreds of MB) is
**not** preinstalled. To turn a box into a wall display:

```bash
# 1. Install the display stack (one-time; only on a box that has a panel).
#    seatd's package creates the `seat` group the seat needs.
sudo apt-get install -y --no-install-recommends cage seatd chromium \
  || sudo apt-get install -y --no-install-recommends cage seatd chromium-browser
sudo systemctl enable --now seatd

# 2. Grant the seat groups via a drop-in. They're added here (not in the
#    shipped unit) because `seat` doesn't exist until seatd is installed —
#    putting it in the base unit would 216/GROUP-fail on headless boxes.
sudo install -d /etc/systemd/system/wattpost-kiosk-display.service.d
printf '[Service]\nSupplementaryGroups=video render input seat\n' \
  | sudo tee /etc/systemd/system/wattpost-kiosk-display.service.d/seat.conf >/dev/null
sudo systemctl daemon-reload

# 3. Turn the feature on (or set kiosk.display_enabled: true in the dashboard)
#    then restart the unit.
sudo systemctl restart wattpost-kiosk-display.service
```

## On-hardware bring-up checklist (NEEDS a panel — can't be verified headless)

Everything above is structurally validated, but the actual rendering + the
board/panel-specific bits can only be confirmed with a display attached:

- [ ] Panel attached, `display_enabled: true`, stack installed → on boot the
      dashboard appears fullscreen (no cursor, no browser chrome).
- [ ] `systemctl status wattpost-kiosk-display` is `active (running)`;
      `journalctl -u wattpost-kiosk-display` shows `launching … /kiosk`.
- [ ] Touch works (official Pi touchscreen): taps/scrolls register.
- [ ] **Rotation** (`display_rotate`): the launcher sets `WLR_RANDR_TRANSFORM`
      best-effort. This is the most likely thing to need tuning — some panels
      rotate cleanly this way, others need a KMS/`config.txt`
      (`video=<connector>:...`) approach instead. Verify per panel.
- [ ] GPU/driver: if the screen is black but the service is "running", check
      `/dev/dri/card*` exists and the `wattpost` user is in `video`,`render`,
      `input`,`seat` groups (the unit sets these).
- [ ] Fonts: install `fonts-dejavu` if glyphs are missing.
- [ ] Headless regression: on a box with NO panel, the unit exits 0 and
      doesn't crash-loop (`systemctl status` shows inactive/dead, not failed).

## Not yet built (v2, needs hardware to design the UX)

- A Settings/dashboard toggle that installs the stack + flips the flag in one
  click (currently the apt-install is a manual opt-in step above).
- Night-blanking schedule (screen off between set hours).
- Rotation via KMS where `WLR_RANDR_TRANSFORM` doesn't take.
