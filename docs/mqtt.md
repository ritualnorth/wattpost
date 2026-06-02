# MQTT-out + Home Assistant

WattPost publishes every metric, on every poll, to a local MQTT
broker. With **Home Assistant auto-discovery** built in. Drop one
config block on your broker, install the Mosquitto add-on in HA,
and every device + metric appears as a first-class sensor.

The same topics power Node-RED automations, custom dashboards, and
any other MQTT consumer on your LAN.

## Quick start with Home Assistant

If you're running HA OS or HA Container on the same network as your
WattPost appliance:

1. **Install the Mosquitto broker add-on** (Settings → Add-ons →
   Mosquitto broker → Install → Start).
2. In Home Assistant, **Settings → Devices & services → Add
   integration → MQTT**. Pick **Use the configured Mosquitto add-on**.
3. On WattPost, **Settings → Integrations → MQTT-out → Configure**.
   - Broker host: your HA box's IP (or `homeassistant.local`)
   - Port: `1883`
   - Username / password: the ones you set in the Mosquitto add-on
   - Topic prefix: `solar` (the default. Leave it alone)
   - **Home Assistant discovery**: on
   - **Test** → "✓ Connected, published 1 retained message"
   - **Save**
4. Restart the daemon.

Within a few seconds, every paired device appears under **Settings →
Devices & services → MQTT** in HA. One *device* per WattPost device
(e.g. "Rover MPPT", "Bank shunt", "JK BMS"), with one *entity* per
metric.

## Topic layout

```
solar/_status                    online / offline (LWT)
solar/<label>/state              full snapshot JSON (retained, every poll)
solar/<label>/<metric>           one value per topic (retained)
```

- `<label>` is whatever you named the device in **Settings → Setup**
  (lowercased, spaces → hyphens). "Bank shunt" becomes
  `solar/bank-shunt/...`.
- `<metric>` is the raw field name: `voltage_v`, `current_a`,
  `soc_pct`, `power_w`, etc.
- The **last will & testament** (`_status`) flips to `offline` when
  the daemon dies; HA marks every entity as Unavailable until the
  next online message lands.

Set `publish_per_metric: false` in the exporter config to drop the
per-metric fan-out and keep only `state`. Useful on broker-limited
setups; HA discovery still works (it parses the JSON payload).

## What discovery actually publishes

For each device + metric pair, the exporter publishes a single
retained message to:

```
homeassistant/sensor/<node>/<label>_<metric>/config
```

With a payload like:

```json
{
  "name": "Bank shunt voltage",
  "state_topic": "solar/bank-shunt/voltage_v",
  "unit_of_measurement": "V",
  "device_class": "voltage",
  "state_class": "measurement",
  "unique_id": "wattpost_bank-shunt_voltage_v",
  "device": {
    "identifiers": ["wattpost_bank-shunt"],
    "name": "Bank shunt",
    "manufacturer": "Victron",
    "model": "SmartShunt 500A"
  },
  "availability_topic": "solar/_status"
}
```

HA picks the right card layout automatically: voltage / current /
power get a graph; SoC gets a percentage gauge; temperature gets °C
formatting; binary outputs get a switch tile.

## Node-RED, Telegraf, custom scripts

Subscribe to `solar/+/+` and you have a fire-hose of every metric on
every poll:

```bash
mosquitto_sub -h <broker> -t 'solar/+/+' -v
```

For long-term retention beyond what WattPost's local SQLite keeps,
Telegraf's MQTT input + InfluxDB output is the well-trodden path.
See [Integrations](/docs/integrations) for the broader REST + SSE
options too.

## Troubleshooting

**HA shows no entities after enabling**. Discovery messages need to
land before HA starts listening. Restart the WattPost daemon to
re-publish the retained config payloads; HA will pick them up
immediately.

**Entities flip to Unavailable randomly**. Broker connection is
dropping. Check broker logs; usually a credential rotation or a
Mosquitto add-on update. The daemon retries every 10 s; once the
broker is reachable again the `online` LWT clears it.

**Want to remove old entities**. Delete the retained config message:

```bash
mosquitto_pub -h <broker> -t 'homeassistant/sensor/<node>/<entity>/config' -r -n
```

HA drops the entity within a few seconds.
