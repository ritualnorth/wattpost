# Smart-scene rules

Threshold alerts say "tell me when something happens." **Rules** say
"do something when it happens." A small if-X-then-Y engine that fires
on every poll, with a tight set of triggers and actions covering
~95% of what off-grid operators actually want to automate.

Available on the **Pro tier** in the cloud and on every appliance
that's paired to a Pro account.

## Anatomy of a rule

> When `metric` on `device` crosses `threshold`, **for** at least
> `for_seconds`, then **do** these `actions`.

Concrete example: *when `soc_pct` on `bank` falls below `30`, for
`120` seconds, then send a `ntfy` warning **and** toggle the Renogy
Rover load output **off**.*

Rules live next to alerts in `config.yaml` under `rules:` — most
people never touch that file because the dashboard's **Settings →
Rules** panel covers the full surface.

## Triggers

| Kind | Example |
|---|---|
| Metric crossing | `bank.soc_pct < 30`, `mppt.pv_power_w > 800` |
| Metric range | `bank.voltage_v outside 12.0–14.6` |
| Stale data | `bank` hasn't reported for >5 min |
| Time of day | `at 22:00 daily`, `between 06:00 and 09:00` |
| Sun event | `at sunset`, `30 min before sunrise` |
| Forecast | `tomorrow.pv_kwh < 1.5` |
| Compound | any AND/OR/NOT of the above |

Triggers re-evaluate on every poll (~60 s). A rule that's already
active stays active until its condition clears + the `cooldown_seconds`
window has elapsed.

## Actions

| Kind | Behaviour |
|---|---|
| Alert | Fire to any [alert transport](/docs/alerts) — ntfy, Discord, Pushover, email, MQTT, webhook |
| Device write | Toggle a writable setting on a device — Rover load output, charge profile, voltage cutoff |
| MQTT publish | Push a custom payload to a topic you pick |
| Webhook | POST JSON to any URL |
| Scene | Combine N actions into one named scene; rules invoke by name |

Device writes use the same [writable settings](/docs/writable-settings)
machinery — Modbus FC06 with a confirmation read on Renogy, BLE
characteristic write on the rest.

## Examples

### Save the bank before midnight

```
when bank.soc_pct < 25 for 60 s
do alert(severity=alarm, transport=ntfy)
   write(renogy_rover.load_output = off)
```

If the bank dips below 25% for a minute and you're not home, the
load output (typically powering DC fridges, lights, USB strips)
goes off — preserving the bank for the inverter / essentials until
solar returns at sunrise.

### Pre-charge before a cloudy day

```
when tomorrow.pv_kwh < 1.5
   and now between 18:00 and 21:00
do write(renogy_inverter.ac_charger = on)
   alert(transport=discord)
```

The Solcast forecast says tomorrow's harvest will be lean; an
hour or two of grid-charging tonight tops the bank up while
electricity is cheap. Discord tells you it ran.

### Dawn wake-up

```
when sun.event = "30 min before sunrise"
do write(renogy_inverter.eco_mode = off)
   mqtt(topic="house/heater/cmd", payload="on")
```

Lifts inverter eco mode (so the kettle works at first light), and
publishes an MQTT command that a Home Assistant automation picks
up to turn on the bedroom oil-filled radiator.

## Testing a rule

Each rule has a **Test** button in the Rules panel. It fires every
action exactly as it would in production — useful for verifying
ntfy reaches your phone, that the load-output write actually toggles
the Rover, etc. The trigger evaluation is skipped, but cooldowns
and dedupe still apply.

## Quiet hours + dedupe

Rules respect the same [quiet hours](/docs/alerts#quiet-hours)
window as alerts: `info` and `warn` alert actions buffer overnight;
device-write actions always fire (because waiting until morning to
disconnect a cratering bank is too late).

## What rules don't do

- **External event triggers** (HA automation firing, an MQTT message
  arriving) aren't sources today. The system pulls from polled
  metrics + forecast + time; if you need a push trigger, run the
  logic in HA and call the WattPost webhook to drive a scene.
- **Loops + persistent state**: rules are stateless except for
  cooldowns. "Charge until 80% then stop" needs two rules (one
  trigger to start, a different one to stop).
- **Cross-site rules**: a rule fires on the appliance it's defined
  on. Cross-site triggers ("any of my 3 sites below 20%") are on
  the Installer-tier roadmap.
