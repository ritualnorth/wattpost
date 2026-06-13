# Hotspot on-device test plan (Pillar 3)

Everything in Pillar 3 is verified in logic + at the HTTP/file level, but
the RF / DNS / multi-OS behaviour needs a real Pi with a WiFi radio to
sign off. Work top-to-bottom; **Section 0 is the point** — those are the
assumptions the off-device tests couldn't reach.

## What you need

- A Pi (built-in WiFi, or a USB WiFi adapter) running the packaged image
  (Pi OS Bookworm + NetworkManager), appliance installed via
  `packaging/install.sh`.
- A phone and/or laptop to join the AP — ideally one each of **iOS,
  Android, Windows** for the captive-portal matrix.
- For the clean-handoff tests: an **Ethernet** uplink to the Pi (or a
  *second* WiFi adapter). Single-radio is the harder path — test both.
- A shell on the Pi (`ssh`/console) for the `nmcli` / file checks.

Key facts to check against:
- Dashboard answers at **`http://10.42.0.1`** while the AP is up.
- Captive drop-in lives at **`/etc/NetworkManager/dnsmasq-shared.d/wattpost-captive.conf`**.
- Timings (from `handoff.py`): AP raises after **~60 s** offline
  (`GRACE_CHECKS=2 × POLL_SECONDS=30`); single-radio probe-drop every
  **~5 min** (`RETRY_AFTER_POLLS=10 × 30 s`).

---

## Results — server-side pass on a real Pi (2026-06-13)

Run against a packaged Pi (built-in BCM4345 WiFi) on an **Ethernet**
uplink, driving the appliance API over localhost. The Ethernet path means
the AP could be raised/dropped without cutting the test's own shell.

**Verified PASS:**
- §0.1 `nmcli` works as the non-root `wattpost` user (polkit rule took).
- §0.2 `wattpost` can write `dnsmasq-shared.d` (dir is `root:wattpost` 0775).
- §0.4 radio supports AP mode (`WIFI-PROPERTIES.AP: yes`) — and the AP
  genuinely raises on `wlan0` (`wlan0:connected:wattpost-hotspot`).
- §1 manual: PUT config → POST /on → AP up, status `active:true`; POST /off
  → AP down, `wlan0:disconnected`, status consistent.
- §3 captive: drop-in `wattpost-captive.conf` written on raise + removed on
  teardown; gateway `http://10.42.0.1/` answers (302 → portal); all five
  probe responders (`/generate_204`, `/hotspot-detect.html`,
  `/connecttest.txt`, `/ncsi.txt`, `/canonical.html`) return the benign
  "online" answer when captive is OFF ("inactive is inert" ✓) and 302 when
  ON. NB OS probes use GET; a HEAD request gets 405 (Litestar doesn't
  auto-add HEAD) — not a real-world path.
- eth0 stayed connected across the whole raise/drop cycle (eth/wifi
  independence, the premise behind §2's clean-drop).

**BUG FOUND + FIXED (commit 2172835):** on a box where WiFi starts
soft-disabled in NM (the default for an Ethernet-only appliance),
`activate()` enabled the radio and *immediately* ran `connection up`,
racing wlan0's unavailable→disconnected transition → "No suitable device
found" → no hotspot. Fixed with `_wait_for_iface_ready()`. This would have
broken the AP for the exact off-grid persona Pillar 3 targets.

**PACKAGING GAP FIXED (same commit):** `iw` wasn't installed by
`install.sh` (Pi OS Bookworm omits it), so `client_count` was permanently
null on real Pis though the Docker image had it. Now installed on the Pi
path.

**STILL NEEDS HARDWARE / A CLIENT (hand to James):**
- §1 reboot persistence (`enabled:true` → AP on cold boot — a *different*
  path via `start()`; cold-boot wlan0 readiness may exceed the 8s wait).
- §2 auto-handoff raise (unplug all networks → AP self-raises ~60s),
  single-radio probe-drop (~5min), manual-untouched, flag-off cleanup —
  all need the uplink physically removed, which would cut a remote shell.
- §3 multi-OS captive sheet auto-open (iOS/macOS/Android/Windows) + DNS
  catch-all resolution from a *joined* client (the Pi has no `nslookup`).

---

## 0. Assumptions to confirm first (highest value)

These are the things that can't be checked without the hardware, and any
one failing changes the design:

- [ ] **`nmcli` works as the non-root `wattpost` user.** The daemon runs
  as `wattpost`. Confirm it can actually drive NM (polkit):
  ```bash
  sudo -u wattpost nmcli -t -f NAME connection show --active   # no auth error?
  sudo -u wattpost nmcli connection up wattpost-hotspot         # (after first config save)
  ```
  If this prompts for a password / fails, NM needs a polkit rule for the
  `wattpost` user — that's a packaging gap to fix.
- [ ] **`wattpost` can write the dnsmasq drop-in dir.**
  ```bash
  sudo -u wattpost touch /etc/NetworkManager/dnsmasq-shared.d/_perm_test && \
    echo OK && sudo -u wattpost rm /etc/NetworkManager/dnsmasq-shared.d/_perm_test
  ```
  (install.sh chgrp+chmod's this dir; confirm it took.)
- [ ] **NM re-reads `dnsmasq-shared.d` when our AP comes up.** We write
  the drop-in *before* `nmcli connection up`; confirm the catch-all is
  actually live (Section 3).
- [ ] **The radio supports AP mode** on this band/region (`band: bg`).
- [ ] **The single-radio probe-drop cadence is acceptable** — a brief AP
  blip every ~5 min while off-grid. If it feels bad in practice, tune
  `RETRY_AFTER_POLLS` or push users toward Ethernet.

---

## 1. Manual hotspot (3a)

- [ ] Settings → WiFi hotspot: set SSID + an 8–63 char password, **Save**.
- [ ] `GET /api/hotspot/status` → `nmcli_available: true`, `configured: true`.
- [ ] **Turn on now** → the SSID appears in a phone's WiFi list within a
  few seconds.
- [ ] Join it with the password → phone gets an IP (`10.42.0.x`).
- [ ] Browse to `http://10.42.0.1` → the dashboard loads.
- [ ] `status` shows `active: true`; `client_count` ≥ 1 (needs `iw`).
- [ ] **Turn off** → SSID disappears; `active: false`.
- [ ] Wrong password is rejected; blank password → open network joins
  with no password.
- [ ] Set `enabled: true`, reboot the Pi → AP comes up on boot.

## 2. Auto-handoff (3b) — local flag

Enable **Auto-enable when offline** (`auto_handoff: true`); make sure
`enabled` (always-on) is **off**.

- [ ] **Raise:** disconnect/forget all known WiFi so the Pi has no
  network. Within **~60 s** the AP comes up by itself (watch
  `journalctl -u wattpost -f` for `no LAN for 2 checks — raised
  fallback AP`).
- [ ] **Clean drop (Ethernet):** plug in Ethernet → AP drops on the next
  tick (`LAN restored (eth) — dropped fallback AP`).
- [ ] **Single-radio recovery:** no Ethernet, AP up; bring a known WiFi
  network back into range. Expect a **probe-drop ~5 min in** (`probe-drop
  — testing for a known network`); NM rejoins the known net; the AP
  stays down. Confirm it doesn't flap when WiFi is *not* available
  (AP just comes back).
- [ ] **Manual is untouched:** turn the AP on by hand, then provide LAN →
  handoff must **not** drop it (`skip:manual` in logs).
- [ ] **Flag off cleans up:** while a fallback AP is up, untick
  Auto-enable → the AP we raised is dropped.

## 3. Captive portal

Enable **Captive portal** (`captive_portal: true`) with auto-handoff or a
manual AP.

- [ ] With the AP up, on the Pi:
  ```bash
  cat /etc/NetworkManager/dnsmasq-shared.d/wattpost-captive.conf
  # → address=/#/10.42.0.1
  ```
- [ ] From a **joined client**, DNS catch-all works:
  ```bash
  nslookup example.com 10.42.0.1     # → 10.42.0.1
  curl -sI http://10.42.0.1/generate_204   # → 302, Location: http://10.42.0.1/
  ```
- [ ] **iOS / macOS:** joining the AP auto-opens the captive sheet showing
  the dashboard.
- [ ] **Android / ChromeOS:** "Sign in to network" notification → opens
  the dashboard.
- [ ] **Windows:** the network shows "needs sign-in" and the browser pops
  the dashboard.
- [ ] **Tear-down:** turn the AP off → the drop-in file is **gone**, and
  a normal (non-captive) join no longer hijacks DNS.
- [ ] **Inactive is inert:** with captive *off* but the box on a normal
  LAN, the probe paths return the benign answers (204 / Success /
  Microsoft Connect Test), i.e. nothing thinks it's a portal.

## 4. Graceful degradation (sanity)

- [ ] On a host **without** NetworkManager (or stop NM), Settings shows
  "Unavailable — NetworkManager not found"; the dashboard + polling keep
  working; nothing 500s.

---

## Recording results

For each failure note: which OS/hardware, the `journalctl -u wattpost`
lines around it, and `nmcli connection show --active` / `nmcli device
status` output. The handoff and captive code paths all log their
decisions, so the journal should explain any surprise.
