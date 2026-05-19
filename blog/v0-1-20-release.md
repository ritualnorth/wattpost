# What shipped in WattPost v0.1.20

v0.1.20 is out. Three new things, all customer-facing: a wizard
hint that catches a Bluetooth ownership conflict we lost a day
to last week, a deeper Bluetooth diagnostic for chipsets with a
specific Linux bug, and a Settings tile to edit polling cadence
and how long the appliance keeps history.

## BT-2 held by another LAN host detection

A Renogy BT-2 dongle is a single-master BLE device. Once one host
holds an active connection, the dongle stops advertising. Other
hosts on the same network can't see it at all. The dongle looks
broken; the BLE stack looks broken; the radio looks broken. The
actual cause is another host being polite first.

We hit this debugging a fresh appliance install where the laptop
running our Docker container had grabbed the dongle and never let
go. The full story is in
[the debugging write-up](/blog/bt2-silent-on-lan).

v0.1.20 ships the detection. When the setup wizard scans and
finds zero Renogy devices, the appliance now probes its local
subnet for other WattPost instances. Each hit is confirmed by
`GET /api/health` returning `service: "wattpost"`. Suspects
appear in a yellow panel above the scan results with the IP
named and a one-paragraph explanation of the single-master rule.

If you've been running a laptop docker against the same dongle as
a Pi appliance, this is the change that surfaces it. The fix is
still on you (stop one of the daemons), but you'll see what to
fix in seconds instead of hours.

## BLE diagnostics for Realtek+BlueZ chipsets

Some BLE chips on the Pi (a common Realtek module shipping in
USB dongles, plus the BLE part of the Pi 4's built-in radio) hit
a known BlueZ 5.72 bug where `bluetoothctl` returns zero device
events even when the radio is fine and bleak (the Python BLE
library WattPost actually uses for polling) is finding plenty.

The result: when a user runs `bluetoothctl scan on` to debug
"why isn't my dongle showing up", it says "scan complete, 0
devices". They conclude the dongle is dead. The dashboard,
meanwhile, keeps working perfectly because it polls via bleak.

v0.1.20 adds a "Run BLE diagnostics" button to the setup wizard
that runs both scanners side-by-side and reports the divergence:

```
bleak: 8 devices · bluetoothctl: 0 devices
verdict: scan_silent_failure
```

With a suggestion that explains the bug, says the appliance is
not affected because it uses bleak directly, and points at where
to look next if the main wizard scan is also failing.

Six verdict cases are covered: `ok`, `scan_silent_failure`,
`bleak_silent_failure`, `bleak_failed`, `bluetoothctl_failed`,
and `no_devices_seen`. Each ships actionable text.

## Editable retention + polling interval

The previous version had four retention windows baked in as
module constants: 7 days of raw 60-second polls, 30 days of
1-minute aggregates, 365 days of 1-hour aggregates, and
indefinite daily summaries. The polling interval was 60 seconds,
also baked in.

Reasonable defaults, but users with Pi 5s wanting denser sampling
or off-grid cabins with months between visits wanting longer
retention had no way to change either without editing source.

v0.1.20 adds a History & polling tile to Settings:

- **Poll interval** · 5 to 3600 seconds
- **Raw samples** · 1 to 90 days
- **1-min aggregates** · raw window to 365 days
- **1-hour aggregates** · 1-min window to 3650 days

Values apply live. The scheduler reads its interval each cycle,
so a Save propagates on the next poll without restarting the
daemon. Retention windows are read on every maintenance pass
(every 10 minutes by default), so the new windows take effect
on the next pass.

Saves also persist to `config.yaml` under a new `history:`
block, so a daemon restart doesn't revert the change.

Tier ordering is enforced server-side: raw must be ≤ 1-min must
be ≤ 1-hour. A fat-finger 0 in the raw box won't wipe history.

## How to get it

Pi installs see "Update available v0.1.20" in Settings → About
within the next poll cycle. Click Update now to apply.

Docker users run `docker compose pull && docker compose up -d`
on the host. The image at `ghcr.io/ritualnorth/wattpost-appliance:latest`
is now v0.1.20.

SD card images are still building (~90 minutes for pi-gen to
finish). When done, the `.img.xz` for fresh installs will be on
the [download page](/download).

## What's next

The hardware-purchase queue is the main constraint right now.
Both writable-settings extensions (#185 Renogy inverter and
#186 Renogy smart shunt) need physical kit in the test lab
before we ship them; guessing at write-register addresses with
a 3 kW inverter on the other end is not how I want to spend a
weekend.

For everything else: the [open issues list](https://github.com/ritualnorth/offgrid-monitor/issues)
on the repo is current. Bug reports and driver requests welcome.
