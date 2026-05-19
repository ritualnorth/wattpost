# What shipped in WattPost v0.1.22

Short release. One feature and one correction.

## The smart-plug adapter the solar-pause rule needed (#163 followup)

v0.1.21 shipped the solar-pause rule engine but its release
notes implied it could drive a Renogy AC charger directly. It
couldn't. Renogy doesn't publish a verified write map for the
AC-charger side of their inverter-chargers, and writing to a
register address we're not sure about is the kind of thing
that bricks customers' gear. So the v0.1.21 rule had no
output to actually drive.

v0.1.22 fixes that with a smart plug as the universal control
surface. WattPost talks directly to the plug over local HTTP,
no MQTT broker, no Home Assistant, no cloud. Two protocols
supported out of the box:

- **Shelly Gen2** (Plug S, Plus, Pro). The recommended option
  for new installs. ~£15 off the shelf, local JSON-RPC, runs
  fully off-grid on your LAN.
- **Tasmota**. For anyone who already flashed their own
  Sonoff / Athom / similar.

The walkthrough is in
[Pause your AC charger with a smart plug](/blog/solar-pause-smart-plug)
end-to-end with Wi-Fi onboarding, the `smart_plugs:` config
block, and the dashboard wiring.

## Correction to v0.1.21's notes

The v0.1.21 changelog and release post have been edited to be
honest about what shipped: the rule engine, not a working
output target. The output target ships here in v0.1.22. If you
read the v0.1.21 post before this correction landed and
thought you could plug your Renogy inverter-charger straight
into the rule, that's the misunderstanding. Smart plug, every
time.

## Get it

Existing Pi installs see the "Update available" badge within
their next poll cycle. Docker users: `docker compose pull &&
docker compose up -d`. Fresh installs: the
[download page](https://wattpost.io/download) serves the
updated SD-card image.
