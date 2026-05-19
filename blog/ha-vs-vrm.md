# WattPost vs Victron VRM in Home Assistant

If you're on a Victron stack with VRM today and looking at Home
Assistant integration, you have two main options: the official
Victron VRM cloud integration (via the
[hass-victron](https://github.com/sfstar/hass-victron) custom
component or the simpler MQTT bridge through a Cerbo GX), or
WattPost reading Victron's local BLE advertisements directly and
publishing to MQTT.

This post is an honest comparison. We make WattPost; we're not
pretending VRM doesn't have advantages. The decision depends
mostly on whether your stack is pure Victron or mixed.

## What each one is

**Victron VRM in HA.** A handful of integrations expose VRM data
to Home Assistant. The official path is through a Cerbo GX (or
GX-class device like the Cerbo GX MK2) running Venus OS, with HA
connecting via the Cerbo's local MQTT broker. The
hass-victron custom component pulls the same data from the VRM
cloud. Both share the same VRM data model and depend on the
Cerbo being your central data aggregator.

**WattPost in HA.** A Raspberry Pi running the WattPost daemon
reads Victron's BLE Instant Readout broadcasts directly off the
radio (no Cerbo needed), polls Renogy and JK BMS gear on the same
box, and publishes everything to whatever MQTT broker your HA
already uses. The
[five-minute setup post](/blog/home-assistant) covers the
install.

## When VRM is the right answer

- **Your stack is 100% Victron** and you have a Cerbo GX already.
  Cerbo is a £400-£500 device but if you already own one you've
  paid the cost.
- **You want writable control** of Victron gear from HA. WattPost
  is read-only on Victron by design (we will not touch a
  customer's Cerbo dbus). VRM-via-Cerbo can change MultiPlus
  charging modes, ESS setpoints, generator start/stop. If those
  flows matter, you need a Cerbo and dbus.
- **You're already paying for VRM Pro.** The premium tier gets
  you longer history retention, custom widgets, multi-site rollup
  inside the VRM portal. WattPost has multi-site rollup at
  wattpost.cloud but no Pro analogue to the VRM widget customisation.

## When WattPost is the right answer

- **Your stack is mixed.** Renogy + Victron + JK BMS on the same
  rig is our sweet spot. Cerbo doesn't read Renogy or JK BMS.
  You can run both (Cerbo for Victron, WattPost for the rest)
  but you now have two parallel data sources to reconcile in HA.
- **You don't have a Cerbo and don't want one.** A Pi 4 / Pi 5
  costs £40-80 versus the Cerbo's £400-500. For read-only
  monitoring of Victron gear, the Pi plus WattPost gets you the
  same dashboard with no Cerbo in the stack.
- **You're off-grid in a way that makes cloud round-trips
  painful.** VRM history is cloud-side; offline you get whatever
  was cached. WattPost stores history locally on the Pi
  indefinitely (subject to the configurable retention windows
  you set in
  [Settings → History](/blog/v0-1-20-release#editable-retention-tiers-and-poll-interval)),
  and HA reads the live state directly off the LAN MQTT broker.
- **You want one box doing two jobs.** WattPost runs alongside
  Home Assistant happily on the same Pi 4 / Pi 5 (HA OS users
  install WattPost as a Docker container; HA Container users add
  WattPost to the existing compose file). VRM-via-Cerbo wants
  its own hardware.

## Head-to-head feature table

| | VRM (via Cerbo) | WattPost |
| --- | --- | --- |
| Victron read | ✓ full dbus | ✓ BLE Instant Readout |
| Victron write | ✓ via Cerbo dbus | ✗ read-only by design |
| Renogy gear | ✗ | ✓ Rover, DCC50S, inverters, shunt |
| JK BMS | ✗ | ✓ read-side |
| Cerbo required | ✓ | ✗ |
| Multi-site fleet | ✓ VRM portal | ✓ wattpost.cloud |
| Local history | ✗ (cloud-side) | ✓ on the Pi |
| Setup time | medium (Cerbo + Venus + creds) | five minutes |
| Hardware cost | £400-500 (Cerbo) | £40-80 (Pi) |
| Yearly cost | VRM Free or Pro (~£20/mo) | local free, cloud £3-6/mo |
| HA integration | MQTT via Cerbo OR cloud poll | MQTT direct |

## What the same data looks like in HA

Both expose the same metrics under different naming conventions.
Substitute one for the other and your Lovelace YAML works:

| Concept | VRM-via-Cerbo entity | WattPost entity |
| --- | --- | --- |
| Battery SoC | `sensor.battery_state_of_charge` | `sensor.<device>_battery_percentage` |
| PV power | `sensor.system_pv_power` | `sensor.<device>_pv_power` |
| Battery voltage | `sensor.battery_voltage` | `sensor.<device>_battery_voltage` |
| Battery current | `sensor.battery_current` | `sensor.<device>_battery_current` |

The big asymmetry is that VRM treats the Cerbo's view as
canonical and exposes everything underneath it through a single
namespace. WattPost exposes each device as its own HA device
because each one is physically separate; on a mixed-stack rig
that's the more honest model anyway.

## What we're worse at

- **Victron writes.** Mentioned above. If you need it, you need
  a Cerbo.
- **Network video stream gateway.** Cerbo also bridges Victron's
  network-video API into HA. WattPost doesn't.
- **Manufacturer support.** Victron has paid technicians. We
  have one engineer reading every support ticket. Sub-day
  response time today is real; it will not be true at 1000
  customers.
- **VRM's historic chart polish.** VRM's web UI has been iterated
  on for a decade. The Lovelace dashboards in
  [our previous post](/blog/ha-dashboards) are good but not as
  pretty as VRM's "advanced" tab without some custom-card
  effort.

## Migration: running both for a week

If you're on VRM today and considering switching, you don't have
to commit. Run both in parallel for a week:

1. Install WattPost via the
   [five-minute setup post](/blog/home-assistant). It uses your
   existing MQTT broker; no conflict with VRM-via-Cerbo even
   if you have one.
2. Both produce HA entities, distinguishable by name pattern.
3. Compare the two on a daily basis for a week. Are the numbers
   close? Is one missing data the other shows? Which dashboard
   do you actually open?
4. After a week, remove the one you don't use.

The data the two integrations collect is independent; running
both does not double-poll the radio or strain the gear.

## Conclusion

Pure-Victron stack with a Cerbo you already own: stay on VRM,
add WattPost only if you want to add Renogy or JK BMS gear
later. Mixed-stack stack or no Cerbo: WattPost is cheaper,
faster to set up, and reads what Cerbo doesn't.

For everyone in between, the parallel-run week is the cheapest
way to make the decision yourself.

The full Home Assistant series starts at
[the five-minute setup](/blog/home-assistant) and continues with
[Lovelace dashboards](/blog/ha-dashboards),
[low-SoC alerts](/blog/ha-low-soc-alerts), and
[the Energy dashboard](/blog/ha-energy-dashboard).
