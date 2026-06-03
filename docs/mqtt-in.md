# MQTT-IN (ingest external sensors)

MQTT-IN lets WattPost **subscribe** to an existing MQTT broker and fold
its sensors into the dashboard as **virtual devices** — they appear on
`/api/devices` and the Devices tab exactly like a BLE- or Modbus-decoded
device, with history and sparklines.

This is the **inbound** direction. It's distinct from the MQTT export
covered in [Integrations](/docs/integrations) and [MQTT](/docs/mqtt),
which *publishes* WattPost's own readings (and Home-Assistant discovery
configs) outward.

## Two ways to map topics

- **Home Assistant discovery (default on).** Point WattPost at your
  HA / Mosquitto broker and it subscribes to the standard
  `homeassistant/+/+/config` discovery topics, learns each entity's
  `state_topic`, and turns it into a metric on a virtual device named
  after the HA device. If you already run Home Assistant, this surfaces
  hundreds of existing entities with one toggle.
- **Manual topic list.** For devices that don't publish discovery
  configs (Shelly gen1, bespoke ESPHome, a microcontroller), map topics
  by hand. Each mapping picks a virtual device label, a metric name, and
  how to read the payload (raw scalar, or a dotted JSON path).

Only `{{ value }}` and `{{ value_json.X }}` value templates are
understood; anything fancier is logged once and skipped.

## Configuration

Add an `mqtt_in:` block to `config.yaml` (a Settings panel reads its
status):

```yaml
mqtt_in:
  enabled: true
  host: 127.0.0.1            # your broker
  port: 1883
  username: ""
  password: ""
  ha_discovery: true         # auto-find HA entities (default true)
  ha_discovery_prefix: homeassistant
  stale_after_seconds: 600   # drop a quiet device after this long
  topics:                    # optional manual mappings
    - topic: shellies/garage/relay/0/power
      label: garage_plug
      metric: power_w
      value_type: scalar     # "scalar" | "json"
    # - topic: sensors/tank
    #   label: water_tank
    #   metric: level_pct
    #   value_type: json
    #   json_path: value.level
```

## Privacy

MQTT-IN only opens an **outbound** connection to the broker *you* point
it at. Nothing leaves your LAN unless you deliberately configure a remote
broker — the same opt-in model as everything else on the appliance.

## API

| Method & path | Purpose |
| --- | --- |
| `GET /api/mqtt_in/status` | Broker host/port, connection state, last error, route + device counts. Returns `{ "configured": false }` when no `mqtt_in:` block is present or it's disabled. |
