# Home Assistant, Grafana, MQTT

WattPost is local-first and **opinionated about staying out of your
way**: every metric is exposed in several formats so you can plug it
into whatever you already use.

> Looking for the **PV forecast** integration?
> That's covered in [PV forecast (Solcast)](#/docs/forecast).

## REST

Read-only JSON on the same daemon that serves this dashboard. Common
endpoints:

```
GET /api/devices
GET /api/devices/<label>/latest
GET /api/devices/<label>/history?metric=…&since=…&until=…&bucket=…
GET /api/devices/<label>/lifetime
GET /api/today
GET /api/load_heatmap?days=30
GET /api/stream                 # Server-Sent Events (live)
```

CORS is open by default (`*`). Call it from a browser dashboard.

## MQTT export

Settings-free if you already have a broker on `127.0.0.1` (the default
config has one). Topics, with prefix `solar`:

```
solar/_status                    online / offline (LWT)
solar/<label>/state              full snapshot JSON (retained)
solar/<label>/<metric>           one value per topic (retained)
```

Set `publish_per_metric: false` in the MQTT exporter config to drop
the per-metric fan-out and only publish `state`.

## Home Assistant

The MQTT exporter has **HA discovery** built in. With the default
config (`ha_discovery: true`), the daemon publishes
`homeassistant/sensor/<node>/<label>_<metric>/config` retained
messages on first poll. HA picks them up and auto-creates one sensor
per metric, grouped under one *device* per WattPost device. Exactly
what you'd see if you'd installed a vendor integration.

Discovery configs include device-class hints (`voltage` / `current` /
`power` / `temperature` / `battery%`), unit_of_measurement, and an
availability_topic so HA shows the entities offline when the daemon
LWT fires.

## Grafana

The simplest path is the built-in **Prometheus exporter**. Enable it
in `config.yaml` alongside (or instead of) MQTT:

```yaml
exporters:
  - id: prom
    type: prometheus
    metric_prefix: wattpost   # optional, default
```

WattPost then serves the latest readings at **`GET /metrics`** on the
dashboard port, in Prometheus text format — read-only, no credentials.
Each numeric per-device reading is a gauge labelled by device, e.g.:

```
wattpost_soc_pct{device="battery_0"} 25.2
wattpost_voltage{device="battery_0"} 13.1
wattpost_pv_power_w{device="rover_mppt"} 248
```

Point Prometheus (or Grafana Agent) at
`http://<appliance>:<port>/metrics`, then add Prometheus as a Grafana
data source. This runs happily next to the MQTT exporter — MQTT for
Home Assistant, Prometheus for Grafana.

Other paths if you'd rather not run Prometheus:

1. **Telegraf** → MQTT input plugin subscribes to `solar/+/+`,
   writes to InfluxDB, Grafana queries InfluxDB. Best for long-term
   retention plus charts beyond what the History tab does.
2. **Direct SQLite**: WattPost's database lives at
   `/var/lib/wattpost/solar-monitor.db` (or wherever you put it).
   Read-only access from Grafana's SQLite datasource works for ad-hoc
   queries.

## Webhooks

The **Webhook** alert transport POSTs a flat JSON event on every
fire:

```json
{
  "rule_id":   "low_soc",
  "name":      "Battery low",
  "severity":  "warn",
  "metric":    "bank.soc_pct",
  "op":        "lt",
  "value":     28.4,
  "threshold": 30,
  "ts":        1778669520
}
```

n8n, IFTTT, Zapier, Lambda. Anything that accepts a POST.
