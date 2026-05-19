# Low-SoC alerts via Home Assistant

A dead battery at four in the morning is the wrong way to find
out the loads outran the solar. This post sets up two Home
Assistant automations that turn the WattPost state-of-charge
sensor into actionable phone notifications: an early warning at
40% and a critical alert at 20%. Both fire once per dip and
unmute themselves after the bank recovers.

## Prerequisites

- WattPost talking to Home Assistant over MQTT
  ([five-minute setup](/blog/home-assistant)).
- The **Home Assistant Companion** app installed on at least
  one phone and signed into your HA instance. The mobile app is
  what receives the push.
- About ten minutes.

## Step 1: Confirm the SoC sensor is reporting

In Home Assistant, open **Developer Tools → States** and search
for `battery_percentage`. You should see your charge controller's
SoC sensor with a numeric value between 0 and 100.

If the value is `unavailable`, your appliance hasn't pushed an
update yet. Wait one poll cycle (default 60 seconds) and refresh.
If it's still unavailable, jump back to the
[MQTT setup post](/blog/home-assistant) and check the broker
connection is green.

The entity ID this post uses is
`sensor.charge_controller_battery_percentage`. If yours differs
(multiple chargers, custom node ID), substitute throughout.

## Step 2: Build the warning automation

Go to **Settings → Automations & scenes → Create Automation →
Create new automation**. Skip the blueprint picker. Pick **Start
with an empty automation**.

In **Edit in YAML** mode, paste:

```yaml
alias: Solar SoC dropped below 40%
description: Warn early so we can dim loads before it gets serious
mode: single
triggers:
  - trigger: numeric_state
    entity_id: sensor.charge_controller_battery_percentage
    below: 40
    for:
      minutes: 5
conditions: []
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "🔋 Battery at 40%"
      message: >
        SoC dropped below 40% and has been there 5 minutes.
        Currently {{ states('sensor.charge_controller_battery_percentage') }}%,
        net {{ states('sensor.charge_controller_battery_current') }} A.
      data:
        tag: wattpost-soc-warn
        priority: high
```

Two notes on the trigger:

- **`for: minutes: 5`** debounces transient dips. A heavy load
  pulling SoC from 41 to 39 for ten seconds during a cooker boost
  is not worth a notification. Five minutes settles real
  trends.
- **`mode: single`** means the automation will not re-fire while
  it's already running. Combined with the `tag` on the notification
  (next section), this keeps your phone from buzzing every
  poll cycle.

Change `notify.mobile_app_your_phone` to match your device. Find
the right service name under **Developer Tools → Actions** and
type `notify.mobile_app_`, autocomplete will show your devices.

Save the automation.

## Step 3: Build the critical automation

Same flow, but with tighter thresholds and a no-quiet-hours flag:

```yaml
alias: Solar SoC critical (below 20%)
description: Wake-the-house alert; lithium below 20% means stop loads
mode: single
triggers:
  - trigger: numeric_state
    entity_id: sensor.charge_controller_battery_percentage
    below: 20
    for:
      minutes: 2
conditions: []
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "🚨 Battery critical"
      message: >
        SoC at {{ states('sensor.charge_controller_battery_percentage') }}%.
        Drop non-essential loads now. Battery V
        {{ states('sensor.charge_controller_battery_voltage') }},
        I {{ states('sensor.charge_controller_battery_current') }} A.
      data:
        tag: wattpost-soc-critical
        priority: high
        importance: high
        channel: alarm_stream
        ttl: 0
```

The extra `importance: high` + `channel: alarm_stream` on Android
makes this bypass Do Not Disturb. iOS users add `interruption-level:
time-sensitive` and `sound: alarm.caf` to the `data:` block for
the equivalent effect.

## Step 4: Verify without waiting for low SoC

Two ways to test:

**Option A, fake the state.** In **Developer Tools → States**,
find `sensor.charge_controller_battery_percentage` and edit it
temporarily to a value below your threshold. HA fires the trigger
on state change. Five minutes later (or two for the critical
one), you should see the notification. Reset the state to a real
value when done; WattPost will overwrite it on the next poll
anyway.

**Option B, call the action directly.** In **Developer Tools →
Actions**, pick `notify.mobile_app_your_phone`, paste the
notification YAML from above, click **Perform action**. The
phone should buzz within seconds.

## Step 5: The "recovered" notification

Optional but useful. When the bank climbs back above 50% after a
warning fired, you probably want to know about it without checking
the dashboard. Third automation:

```yaml
alias: Solar SoC recovered above 50%
description: Confirms loads dropped and panels caught up
mode: single
triggers:
  - trigger: numeric_state
    entity_id: sensor.charge_controller_battery_percentage
    above: 50
    for:
      minutes: 10
conditions:
  - condition: template
    value_template: >-
      {{ as_timestamp(now()) - as_timestamp(
          state_attr('automation.solar_soc_dropped_below_40',
                     'last_triggered') or '1970-01-01') < 14400 }}
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "✅ Battery recovered"
      message: >
        SoC back to {{ states('sensor.charge_controller_battery_percentage') }}%.
```

The condition checks that the warning automation has fired in the
last four hours. Without that condition, the recovery notification
would fire every time the bank climbed above 50% from any prior
state, which is constant noise during normal daytime cycling.

## Bonus: a quieter version that uses the WattPost alert engine

WattPost has its own alert engine that fires across six transports
(ntfy, Discord, Pushover, email, MQTT, webhook). For the SoC
case, you can configure both:

- **WattPost-side rule** for redundancy: even if Home Assistant is
  offline, ntfy or Discord still get the page.
- **HA-side automation** for routing into your existing HA
  dashboard / mobile-app flow.

Set it on the appliance at Settings → Alerts → Add rule → SoC
below X. The transports are configured in the same panel.

> Note: HA outages happen. The Companion app's push depends on a
> chain involving Firebase / APNS, Cloud Nabu Casa or your own
> remote-access setup, and the integration's connection to your
> HA instance. The WattPost-side alert engine talks directly to
> ntfy or Discord from the appliance, with a separate failure
> mode. Belt and braces.

## Conclusion

Two automations, ten minutes of work, no more 4am-dead-battery
mornings. The same pattern (numeric trigger + `for:` debounce +
notify) applies to temperature alerts, charger over-current
alerts, BMS cell-imbalance alerts, anything WattPost surfaces as
a numeric sensor.

The
[Lovelace dashboards post](/blog/ha-dashboards) covers what to
show on screen; this post covers what to push to your pocket.
Next in the HA series:
[adding WattPost to Home Assistant's Energy dashboard](/blog/ha-energy-dashboard).
