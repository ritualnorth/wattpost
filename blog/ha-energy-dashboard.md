# Add WattPost to Home Assistant's Energy dashboard

Home Assistant ships a built-in Energy dashboard. Give it the
right sensors and you get a polished view of solar production,
home consumption, and balance over time, with the same daily and
monthly aggregation you would expect from a paid app. This post
wires WattPost's energy counters into that dashboard.

## Prerequisites

- WattPost talking to Home Assistant
  ([five-minute setup](/blog/home-assistant)).
- A WattPost appliance version **0.1.20 or later**. Earlier
  versions published `energy_*_wh` sensors without the
  `device_class: energy` discovery hint, which meant HA wouldn't
  list them as Energy sources. The v0.1.20 release notes call
  this out specifically.
- About ten minutes, plus 24 hours for HA to start showing real
  data once the sensors are wired (the Energy dashboard depends
  on long-term statistics, which take time to build up).

To confirm your version, open **Settings → About** on the
WattPost dashboard. The version pill at the top reads `v0.1.20`
or higher.

## Step 1: Confirm the energy sensors are discoverable

The Energy dashboard reads two kinds of sensors:

- **Cumulative** counters that only go up (or reset to zero):
  `state_class: total_increasing`, unit in `Wh` or `kWh`.
- **Decimal kWh** equivalents, HA happily handles the `Wh`
  unit and divides by 1000 internally, so you don't need to
  multiply yourself.

WattPost publishes both kinds. The relevant ones for the Energy
dashboard:

- `sensor.charge_controller_energy_total`, lifetime PV production
  in Wh (cumulative; counts up indefinitely).
- `sensor.charge_controller_energy_today`, today's PV production
  in Wh (resets to zero at midnight; HA's `total_increasing`
  handles the reset automatically).
- `sensor.charge_controller_consumption_today`, today's load
  consumption (same daily-reset shape).

Go to **Developer Tools → States**, search for
`energy_total`, and confirm the **attributes** panel on the right
shows:

```yaml
device_class: energy
unit_of_measurement: Wh
state_class: total_increasing
```

All three keys must be present. If `device_class` is missing,
you are still on an older WattPost; update first.

## Step 2: Open the Energy dashboard configuration

Click the **Energy** entry in the Home Assistant sidebar. The
first time you visit, HA walks you through a setup. If you have
been there before, click the **gear icon → Configure**.

You'll see four sections:

1. **Electricity grid**, for grid-tied imports/exports.
2. **Solar panels**, what we want.
3. **Home battery storage**, what we also want.
4. **Gas / water**, skip.

## Step 3: Wire up solar production

In the **Solar panels** section, click **Add solar production**.

Pick `sensor.charge_controller_energy_total` from the dropdown.
This is the lifetime PV-production counter. HA computes the
hourly / daily / monthly deltas from it automatically; you don't
need to do any maths.

> Note: HA actually prefers cumulative-forever counters over
> daily-reset ones. Both work, but using the `_total` variant
> means HA's reset-detection logic never has to guess. Use
> `_today` only if your charger doesn't expose a lifetime value
> (Renogy Rover and DCC50S do; Victron SmartSolar models do via
> Yield-total).

Optionally, also link the live PV power sensor under **Forecast
provider → Custom**. WattPost ships its own solar forecast at
`sensor.charge_controller_pv_power` paired with the Open-Meteo
PV estimator, but HA's Energy panel can take any forecast
provider; the Solcast integration also works fine if you have
your own Solcast hobbyist key.

## Step 4: Wire up battery storage

In the **Home battery storage** section, click **Add battery
system**.

Two sensors needed:

- **Energy going IN to the battery**, for a charge-controller-
  only setup like a basic Renogy install, you can use a derived
  cumulative `charging_ah_today` × bank voltage, but the simpler
  option is to add a Renogy / Victron smart shunt to your bank
  and use its
  `sensor.<shunt>_consumed_amp_hours` × voltage.
- **Energy going OUT of the battery**, same shunt, the
  discharged Ah counter.

If you only have a charge controller (no shunt), skip this section
for now and revisit when you add a shunt. The Solar panels
section alone gives you the headline production view.

## Step 5: Save and wait

Click **Save**. HA pulls in the initial state.

The Energy dashboard will look mostly empty for the first 24
hours. This is normal. Home Assistant's long-term statistics
system aggregates raw state changes into hourly buckets, and
those buckets don't start producing dashboard-ready charts until
they have a full day of data to work with. Come back tomorrow.

Within the first hour you should at least see the headline
"Solar produced today" tile populate.

## Step 6: Read the dashboard

Once the data accumulates, the Energy dashboard gives you:

- **Today's solar production** as a kWh number plus a sparkline.
- **Source / use breakdown** stacked-area chart.
- **Daily / monthly / yearly tabs** for longer trends.
- A **carbon-emissions estimate** if you have an electricity
  provider configured (skip for off-grid; the number is
  meaningless without a grid baseline).

The dashboard is read-only; tweaks happen back in the **gear
icon → Configure** panel.

## Bonus: per-source breakdown for mixed-stack rigs

If you have multiple solar inputs (say a Renogy Rover on roof
panels plus a Renogy DCC50S DC-DC on an alternator), HA lets you
add multiple "solar production" sources. Each one's cumulative
counter is treated separately, and the dashboard sums them. Pick
the right `*_energy_total` sensor for each device in **Add solar
production**.

## Troubleshooting

> **"My sensor doesn't show up in the dropdown."** HA only lists
> sensors with `device_class: energy` AND `state_class:
> total_increasing` AND a `Wh` or `kWh` unit. If yours is missing,
> verify all three in **Developer Tools → States** as in step 1.
> If any are absent, update WattPost to v0.1.20 or later.

> **"The dashboard shows the wrong total."** Almost always a
> reset-detection issue. Stop the dashboard's
> `homeassistant.statistics` recorder, delete the long-term
> statistics for that sensor (Developer Tools → Statistics → pick
> the sensor → **Fix issues**), and let HA rebuild from current
> state.

> **"Numbers are double the expected value."** You probably
> wired both the `_today` and the `_total` sensors as separate
> production sources. Remove the duplicate.

## Conclusion

The Energy dashboard is the polished read-only view of your
solar history that customers ask us about most. Now it's three
clicks away from any HA install paired with a WattPost appliance.

Next in the HA series:
[WattPost vs Victron VRM in Home Assistant](/blog/ha-vs-vrm) ,
a head-to-head comparison for anyone still on VRM and considering
switching.
