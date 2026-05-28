# WattPost

I built this to monitor my off-grid workshop without handing anyone
my data. It runs on a Raspberry Pi or any Linux Docker host, polls
solar/battery gear over Bluetooth (or wired serial when BLE is
flaky), and serves a live dashboard.

Local-first. No cloud account required to see your own battery.

[Live demo](https://demo.wattpost.io) ·
[Product site](https://wattpost.io) ·
[Docs](https://wattpost.cloud)

## What it covers

BLE-first multi-vendor end of off-grid setups:

- **Renogy** Rover MPPTs, DCC DC-DC combos, smart batteries,
  shunts, 1-3 kW inverter-chargers (BT-1/BT-2 BLE or USB-RS485)
- **Victron** SmartShunt, BMV-7xx, SmartSolar MPPT, Orion DC-DC,
  Blue Smart AC Charger, SmartLithium, Lynx Smart BMS,
  SmartBatteryProtect, Phoenix Inverter (BLE Instant Readout or
  VE.Direct wired)
- **JK BMS** native BLE (JK02-24S, JK02-32S, JK04)
- **JBD / Overkill, Daly, AiLi, Junctek** BLE shunts and BMSes
- **EPEVER / EPSolar** Tracer MPPTs over USB-RS485
- **Mopeka Pro / Check Pro** tank sensors, **Govee + Ruuvi**
  ambient probes
- **MQTT-in** for Shelly EM, Home Assistant entities, anything
  publishing JSON

Three hybrid inverter families also landed, marked experimental
until a customer with real hardware confirms them:
**Voltronic/Axpert/MPP**, **EG4 XP/Luxpower**, and
**Deye/Sunsynk/Sol-Ark** (1P + 3P). For a Sol-Ark 15K or EG4
18kPV install where the inverter IS the system,
[Solar Assistant](https://solar-assistant.io) is the better
tool. WattPost fits the mixed-stack van / cabin / boat builds
where no single device is the brain.

Full vendor + model list: [supported-hardware.md](docs/supported-hardware.md).

## Install

The Pi path is an SD image you flash with Raspberry Pi Imager,
boot, and follow the setup wizard:
[wattpost.io/download](https://wattpost.io/download).

The Docker path is a one-file compose with optional BLE
passthrough: [docker-install.md](docs/docker-install.md).

Developer setup:

```bash
git clone git@github.com:ritualnorth/wattpost.git
cd wattpost
python3 -m venv .venv && .venv/bin/pip install -e .
cp config.example.yaml config.yaml
# edit config.yaml with your transports + devices
.venv/bin/solar-monitor serve --config config.yaml
```

## Cloud (optional)

Local-only is free forever. The cloud add-on at
[wattpost.cloud](https://wattpost.cloud) adds remote access,
multi-site fleet view, push notifications, and encrypted
backups. £6/mo. Most people don't need it.

## Docs

- [Adding a vendor](docs/adding-a-vendor.md)
- [Architecture](docs/architecture.md)
- [Supported hardware](docs/supported-hardware.md)
- [Release pipeline](docs/release-pipeline.md)
- [Changelog](CHANGELOG.md)

## License

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). The full
source ships under `/opt/wattpost-src` on every installed appliance.
