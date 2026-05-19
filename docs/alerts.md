# Alerts

WattPost evaluates **alert rules** on every poll cycle (~60 s) and fans out matching events to **transports**. Rules + transports are entirely local in the free tier; the cloud adds heartbeat-stale alerts (only the cloud can tell you when the appliance itself is dead).

## A rule, abstractly

> When `metric` on `device` is `op` than `threshold` for at least `cooldown` seconds, fire `severity` to these `transports`.

Concrete example: *when **soc_pct** on **bank** is below **30** for 5 minutes, fire **warn** to **ntfy_garage + email_owner**.*

Rules are defined in `config.yaml` under `alerts:` or, more typically, edited via the dashboard's **Settings → Alerts** panel.

## Severity

| Level | When to use | Quiet-hours behaviour |
| - | - | - |
| `info` | "FYI. Load tile crossed a milestone" | Buffered, dropped at the next window end |
| `warn` | "Battery's getting low" | Buffered, replayed when quiet hours end |
| `alarm` | "Cells at 2.7 V. Disconnect now" | Always pages through immediately |

## Transports

All are free + local:

- **ntfy**. Push to your phone via [ntfy.sh](https://ntfy.sh) (or your own ntfy server)
- **Discord webhook**. Drop into a channel
- **Pushover**. Paid app, very reliable
- **Email** (SMTP). Your relay, your Gmail app password, your ISP, etc.
- **MQTT**. Publish to a topic; Home Assistant / Node-RED / your own scripts can subscribe
- **Generic webhook** · `POST` JSON to any URL

The Cloud tier adds:

- **Heartbeat-stale alerts**. Server-side, fires email when an appliance's heartbeat is overdue
- **SMS via Twilio** (coming). On our credit, no per-message billing for you
- **Cross-site rules** · "any of my N appliances below 30%"

## Quiet hours

Configurable per-appliance overnight window (e.g. 22:00 → 07:00). During quiet hours, `info` and `warn` alerts buffer; they're replayed (or dropped if too stale) when the window ends. `alarm` severity always pages through.

Quiet hours are local to the appliance's timezone. Set in **Settings → System → Locale + clock**.

## Cooldown

Each rule has a per-rule cooldown timer. After firing once, the same rule won't fire again until `cooldown_seconds` have elapsed AND the condition has cleared and re-entered the alert state. Stops a SoC oscillating around 30% from sending 40 messages an hour.

## Testing

Settings → Alerts → per-rule **Test** button fires a synthetic event through the transport without changing daemon state. Useful for verifying the ntfy URL, Discord webhook, SMTP creds, etc.

## What we don't do

- **Vendor cloud push** (Renogy app, Victron VRM): out of scope. Their auth is proprietary and tied to your account on their server.
- **Voice calls / phone tree**: SMS via Twilio is the planned escalation path; voice is too noisy for what's mostly information.
