# Home Assistant Lovelace dashboards for off-grid solar

Once WattPost is publishing to your MQTT broker every metric your
solar gear reports lands in Home Assistant as a proper sensor.
This post turns that pile of sensors into three usable dashboards:
a daily-driver overview, a today-vs-yesterday energy view, and a
deep-dive battery dashboard. Every card here is built into core
Home Assistant. No custom integrations, no HACS, just paste the
YAML.

If you have not wired WattPost to Home Assistant yet, the
[five-minute setup guide](/blog/home-assistant) is the place to
start.

## Prerequisites

- A WattPost appliance paired with at least one Renogy, Victron,
  or JK BMS device.
- Home Assistant with the WattPost MQTT integration showing the
  expected sensors under **Settings → Devices & services → MQTT**.
- About fifteen minutes to paste three blocks of YAML and tweak
  the names to match your install.

The sensor names in this post assume the default WattPost MQTT
config (`topic_prefix: wattpost`, `ha_node_id: wattpost`). If
yours differ, find-and-replace `charge_controller_` with the
matching prefix from your **Settings → Devices & services →
MQTT** detail page.

## Step 1: Add a new dashboard

In Home Assistant, go to **Settings → Dashboards → Add Dashboard**.
Pick **New dashboard from scratch**. Name it **Solar**. Pick the
solar-panel icon. Tick **Show in sidebar**.

The dashboard opens empty.

## Step 2: Build the Overview view

Click **Edit dashboard** in the top right, then **+ Add card**.
Pick **Manual** and paste this:

```yaml
type: gauge
entity: sensor.charge_controller_battery_percentage
name: State of charge
min: 0
max: 100
severity:
  green: 50
  yellow: 25
  red: 0
```

That gives you the headline donut: state of charge at a glance,
red below 25%, amber 25-50%, green above 50%. Adjust the
thresholds to your bank's comfort zone if 50% is too generous.

Add a second card, also manual:

```yaml
type: horizontal-stack
cards:
  - type: entity
    entity: sensor.charge_controller_pv_power
    name: Solar in
    icon: mdi:solar-power-variant
  - type: entity
    entity: sensor.charge_controller_load_power
    name: Load out
    icon: mdi:power-plug
  - type: entity
    entity: sensor.charge_controller_battery_current
    name: Battery
    icon: mdi:battery-charging
```

A three-up tile of live power flow. Solar in, load out, battery
current. The battery tile shows positive when charging and
negative when discharging, matching the convention WattPost uses
on its own dashboard.

Now a 24-hour history graph:

```yaml
type: history-graph
title: Last 24 hours
hours_to_show: 24
entities:
  - entity: sensor.charge_controller_battery_percentage
    name: SoC
  - entity: sensor.charge_controller_pv_power
    name: PV power
```

Save the dashboard. You now have a working at-a-glance view.

## Step 3: Build a Today view

Add a second view in the dashboard editor (**+ Add view** at the
top). Title: **Today**. Path: `today`. Icon: `mdi:calendar-today`.

First card on the new view, three energy stats:

```yaml
type: horizontal-stack
cards:
  - type: entity
    entity: sensor.charge_controller_energy_today
    name: Generated
    icon: mdi:lightning-bolt
  - type: entity
    entity: sensor.charge_controller_consumption_today
    name: Consumed
    icon: mdi:home-import-outline
  - type: entity
    entity: sensor.charge_controller_charging_ah_today
    name: Charged Ah
    icon: mdi:battery-plus
```

Then today's peaks:

```yaml
type: entities
title: Today's peaks
entities:
  - entity: sensor.charge_controller_max_charging_power_today
    name: Max PV power
  - entity: sensor.charge_controller_max_discharging_power_today
    name: Max load power
```

And a week-long PV trend (this card depends on Home Assistant's
long-term statistics, which take 12-24 hours to start populating
after the sensor first appears):

```yaml
type: statistics-graph
title: PV power over the last week
chart_type: line
days_to_show: 7
stat_types:
  - mean
  - max
entities:
  - sensor.charge_controller_pv_power
```

## Step 4: Build a Bank view

Third view. Title: **Bank**. Path: `bank`. Icon: `mdi:car-battery`.

A voltage gauge with thresholds tuned for a 12 V lithium bank:

```yaml
type: gauge
entity: sensor.charge_controller_battery_voltage
name: Battery voltage
min: 10
max: 16
needle: true
severity:
  green: 12.4
  yellow: 11.5
  red: 0
```

The full charge profile, read-only:

```yaml
type: entities
title: Bank charge profile
entities:
  - entity: sensor.charge_controller_boost_voltage
    name: Boost
  - entity: sensor.charge_controller_float_voltage
    name: Float
  - entity: sensor.charge_controller_boost_recovery
    name: Boost recovery
  - entity: sensor.charge_controller_equalize_voltage
    name: Equalize
  - entity: sensor.charge_controller_low_voltage_disconnect
    name: Disconnect
  - entity: sensor.charge_controller_low_voltage_reconnect
    name: Reconnect
```

These values are read from the charge controller every poll cycle.
If you want to **change** them, do it from the WattPost dashboard
on the appliance itself (Settings → Devices → your charger). The
new values flow back into Home Assistant on the next poll.

Finally, a 48-hour view of voltage and temperature side by side
so you can spot the overnight dip pattern:

```yaml
type: history-graph
title: Voltage / temperature trends
hours_to_show: 48
entities:
  - entity: sensor.charge_controller_battery_voltage
  - entity: sensor.charge_controller_battery_temperature
  - entity: sensor.charge_controller_controller_temperature
```

## Step 5: Tweak and ship

Save the dashboard. Open it from the sidebar. The three views are
tabs across the top.

A few things you might want to change:

- **SoC thresholds.** The defaults (red <25%, amber <50%) are
  conservative. Lead-acid users should pull both higher; lithium
  with a generous reserve can drop them.
- **Voltage range.** The Bank gauge is set for a 12 V bank. If
  you have 24 V or 48 V, double or quadruple the `min` and `max`.
- **Card colour.** Add `theme: <name>` to any card to pick a
  different theme. The
  [Lovelace theming docs](https://www.home-assistant.io/integrations/frontend/)
  cover the palette.
- **Mobile layout.** The default cards rearrange themselves on
  narrow screens. If you want a different mobile layout
  specifically, use the `grid` card with `columns: 1`.

## Bonus: visual flair via mini-graph-card

Stock cards work. If you want a denser one-line SoC trace inline
with the rest of the overview, install
[mini-graph-card](https://github.com/kalkih/mini-graph-card) via
HACS and replace the history-graph card on the Overview view
with:

```yaml
type: custom:mini-graph-card
name: State of charge
entities:
  - entity: sensor.charge_controller_battery_percentage
hours_to_show: 24
line_width: 3
font_size: 75
points_per_hour: 4
```

It is the same data, drawn in a way that fits better as a header
strip.

## Conclusion

You now have three Lovelace views covering the at-a-glance
overview, daily energy summary, and battery deep-dive. Everything
is plain core Home Assistant; nothing breaks on the next HA
update. The same YAML works for Renogy MPPT, Victron BLE, JK
BMS, or any combination because the sensor names follow the same
`<device>_<metric>` pattern across vendors.

Next up in the Home Assistant series: turning the same SoC sensor
into a phone notification when the bank drops below your comfort
line. That post is at
[Low-SoC alerts via Home Assistant](/blog/ha-low-soc-alerts).
