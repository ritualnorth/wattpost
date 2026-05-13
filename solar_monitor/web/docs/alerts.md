# Alerts & notifications

WattPost evaluates a set of rules after every poll. When a rule
fires, it's dispatched to one or more **transports** — channels you've
configured to receive the events.

## Transports

Configure these in **Settings → Alerts → Transports**.

| Type | What it does | Internet needed? |
|---|---|---|
| **ntfy** | Push to a topic on ntfy.sh (or your own server). Install the ntfy iOS / Android app to receive. | Yes (or self-host). |
| **Discord** | POST to a channel webhook. No bot setup. | Yes. |
| **Webhook** | POST a JSON payload to any URL — Zapier, n8n, your own server. | Depends on target. |
| **Email (SMTP)** | Send via your own SMTP creds (Gmail app password, ISP relay, AWS SES). | Yes. |
| **MQTT (LAN)** | Publish to a local MQTT broker so Home Assistant / Node-RED picks it up. | **No** — pure LAN. |

> **Off-grid?** Combine WattPost's MQTT transport with a local
> Mosquitto broker on the same Pi and an HA install pointed at it. You
> get push-style alerts in HA's app with zero internet.

## Rules

Configure in **Settings → Alerts → Alert rules**.

Each rule:

- **ID**: stable identifier (no spaces, no fancy chars).
- **Name**: shown in the notification title.
- **Metric**: dotted path into the snapshot context.
  - Common: `bank.soc_pct`, `bank.netW`, `bank.meanV`,
    `bank.worst_pack_drift_v`, `aggregate.max_cell_drift_v`.
  - Per-device: `devices.<label>.<metric_key>` —
    e.g. `devices.battery_0.cell_drift_v`.
- **Op**: `lt`, `lte`, `gt`, `gte`, `eq`, `neq`.
- **Threshold**: numeric.
- **Severity**: `warn` or `alarm`. Alarm gets ntfy `Priority: high`,
  red Discord embed, ALARM in the subject.
- **Cooldown (min)**: don't re-fire the same rule for this many
  minutes after a previous fire. Stops a flapping voltage from
  spamming notifications.
- **Send via**: one or more configured transports.

## Test

Every rule row has a **Test** button — sends one event through the
rule's configured transports without waiting for the metric to
actually trip. Use it after creating a rule to confirm the channel is
wired up correctly.

## Example: low-battery + cell-drift on iPhone via ntfy

1. Add a transport: type **ntfy**, topic `my-rv-alerts-${something-random}`.
2. Install the **ntfy** app on your phone, subscribe to the same topic.
3. Add two rules:
   - `low_soc`: metric `bank.soc_pct`, op `lt`, threshold `30`,
     severity `warn`, transports `[ntfy_main]`.
   - `cell_drift`: metric `aggregate.max_cell_drift_v`, op `gt`,
     threshold `0.05`, severity `warn`, transports `[ntfy_main]`.
4. Hit **Test** on each.
