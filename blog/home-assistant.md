# How to add WattPost to Home Assistant in five minutes

WattPost ships a first-class MQTT-out stream with Home Assistant
auto-discovery built in. Once it's enabled, every metric your
appliance reads (state of charge, voltages, currents, power flow,
solar yield, alerts) shows up in Home Assistant as a real sensor
with the right unit and device class, grouped under your
appliance's name. No YAML editing. No helper scripts.

This guide walks you from a fresh WattPost install to a working
Home Assistant integration in under five minutes.

## Prerequisites

- A WattPost appliance with at least one paired device. If you
  haven't set yours up yet, the
  [Pi install guide](/blog/first-install) is the place to start.
- A running Home Assistant instance (Container, OS, Supervised,
  or Core all work).
- The Home Assistant **MQTT integration** installed. Most
  modern HA installs ship it. If you don't have one, the
  [Mosquitto broker](https://www.home-assistant.io/integrations/mqtt/)
  add-on is the easiest path.
- The Pi and the Home Assistant host on the same LAN, or a
  reachable MQTT broker if you're running them on separate
  networks.

## Step 1: Get your MQTT broker details

You need three values: the broker's hostname (or IP), the port
(usually 1883), and any credentials you've set on it.

If you're running the Home Assistant Mosquitto add-on, the
broker hostname is `core-mosquitto` from inside HA, but **from
WattPost you need the LAN IP** of your Home Assistant host
because WattPost runs in its own process on the Pi. Find that
IP in HA at **Settings → System → Network**, or on your router's
client list.

Note the username and password you set up when you installed
Mosquitto. If you didn't set any, that's a problem: an open
broker on your LAN is a foot-gun. Set credentials before you
continue.

## Step 2: Open WattPost's MQTT settings

On your WattPost dashboard, go to **Settings → Integrations →
MQTT**.

You should see a panel with a single toggle ("Publish to MQTT")
and a small form: host, port, username, password, base topic.

## Step 3: Fill in the broker details

- **Host**: the IP of your Home Assistant host (or your separate
  Mosquitto host if you have one).
- **Port**: `1883` for plain MQTT, `8883` for TLS. Default
  Mosquitto on Home Assistant is `1883`.
- **Username**: the MQTT user you created.
- **Password**: that user's password.
- **Base topic**: leave as the default (`wattpost/<appliance-id>`).
  This is the topic prefix WattPost publishes under. Change it
  only if you already have something else using that namespace.

Tick **Publish to MQTT** and click **Save**.

WattPost will try to connect immediately and surface any failure
inline. A green tick means the broker accepted the connection.
A red banner means the credentials or hostname are wrong.

## Step 4: Watch the sensors land in Home Assistant

WattPost publishes an MQTT discovery message for each metric.
Home Assistant picks the discoveries up automatically and
creates a sensor entity per metric, grouped under a device
named after your appliance.

Open Home Assistant and go to **Settings → Devices & services
→ MQTT**. Within thirty seconds you should see a new device
named after your appliance. Click into it.

You will see a sensor per metric: state of charge, voltage,
current, power, temperature, today's PV in, today's load out,
runtime forecast, and one per device-specific field your gear
reports (charger state, BMS cycle count, alerts active, and so
on). Each sensor has the right unit (`%`, `V`, `A`, `W`, `°C`,
`Wh`) and the right device class so the History tab graphs
look correct without any extra config.

## Step 5: Build a dashboard card

The simplest useful card is a battery summary tile. Add a new
card on any Home Assistant dashboard, pick **Entities**, and
select these sensors from your WattPost device:

- `<appliance>_state_of_charge` (your headline number)
- `<appliance>_net_power_w` (positive = charging, negative =
  discharging)
- `<appliance>_battery_voltage_v`
- `<appliance>_today_pv_in_wh`
- `<appliance>_today_load_wh`

That's a complete energy overview in a single tile, built from
sensors that didn't exist five minutes ago.

For something nicer-looking, the
[mini-graph-card](https://github.com/kalkih/mini-graph-card)
custom card paired with the SoC sensor gives you a 24-hour
trace on a single line of YAML.

## Step 6: Automations

Because every metric is a regular HA sensor, automations work
the obvious way. A few that customers actually run:

- **Low-SoC notification**. Trigger: state of
  `<appliance>_state_of_charge` drops below `20`. Action:
  notify a phone via the Home Assistant mobile app.
- **Excess solar dump**. Trigger: state of
  `<appliance>_pv_power_w` above `1500` AND
  `<appliance>_state_of_charge` above `95`. Action: switch on
  an immersion-heater plug to soak the surplus.
- **Quiet hours**. Use the same SoC sensor as the trigger for a
  smart-scene that disables non-essential loads overnight when
  the bank is below 40%.

WattPost also has its own local alert engine for the same kinds
of rules. The HA route is the right one when you want the
notification to thread through your existing HA automations,
phone presence detection, or third-party integrations that HA
already has wired up.

## Troubleshooting

> **Note:** WattPost is the *publisher*. Sensors only appear in
> HA once at least one poll cycle has completed AFTER you enable
> MQTT. If the device shows up empty, wait the appliance's poll
> interval (default 60 s) and refresh.

**Connection refused.** Most often the IP is wrong or Mosquitto
isn't listening on the network interface WattPost is reaching.
On the HA Mosquitto add-on, check the **Configuration** tab and
confirm `0.0.0.0` is in `customize → active: true` listeners,
or that the broker is bound to all interfaces.

**Authentication failed.** Recreate the MQTT user in HA. Some
characters in passwords confuse client libraries; stick to
alphanumerics for the first integration and harden later.

**Device shows up but no sensors.** Open the MQTT integration's
configuration in HA, click **Re-configure**, and toggle
**Enable discovery** off and on. HA caches discovery payloads
aggressively; this forces a re-read.

**Discovery topic clash.** If you have another integration
publishing to `homeassistant/` (the discovery prefix), WattPost
will live alongside it without conflict. The two namespaces are
disjoint by design.

## Conclusion

You now have every WattPost metric flowing into Home Assistant
as proper sensors, grouped under your appliance as a device,
ready to drop into dashboards or trigger automations.

The full MQTT topic schema is in the
[MQTT integration docs](/docs/mqtt) for anyone who wants to
build directly on the topic stream without going through Home
Assistant's discovery layer. The
[alerts docs](/docs/alerts) cover the local alert engine if you
want notifications without an MQTT broker in the mix at all.
