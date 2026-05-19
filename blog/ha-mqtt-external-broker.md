# Connect WattPost to Home Assistant when you can't use the Mosquitto add-on

The [five-minute Home Assistant guide](/blog/home-assistant)
assumes you're running Home Assistant OS or Supervised and can
install the Mosquitto broker add-on with two clicks. That covers
most people. It doesn't cover three groups, all of which we hear
from in support:

- Anyone running **Home Assistant Container** (HA in Docker
  without the supervisor). No add-on system, so no one-click
  Mosquitto.
- Anyone running **Home Assistant Core** directly (pip install
  in a venv). Same story.
- Anyone running a **standalone Mosquitto** broker elsewhere on
  the LAN (a NAS, a separate Pi, a VPS) because it predated their
  Home Assistant install or because they already use MQTT for
  Zigbee2MQTT and want one broker for the whole house.

The MQTT setup on the WattPost side is identical for all three.
The work is on the broker and HA sides: stand up Mosquitto
yourself, point both WattPost and Home Assistant at it, and add
the MQTT integration to HA without the add-on shortcut.

This guide walks all three deployments.

## Prerequisites

- A WattPost appliance with at least one paired device.
- Home Assistant Container, Core, or any HA install where the
  Mosquitto **add-on** is not available.
- A Linux host that can run Docker, or a Pi / NAS / VPS that
  already runs Docker. The broker doesn't have to be on the same
  machine as Home Assistant.
- LAN connectivity between WattPost, Home Assistant, and your
  Mosquitto host. They don't all need to be on the same machine,
  but they do need to reach each other on TCP 1883.

## Step 1: Stand up Mosquitto in Docker

The eclipse-mosquitto image is the canonical Mosquitto build,
maintained by the broker's own team. Create a folder for your
broker's data and config first:

```bash
mkdir -p ~/mosquitto/{config,data,log}
```

Drop a minimal `mosquitto.conf` into `~/mosquitto/config/`:

```
listener 1883 0.0.0.0
persistence true
persistence_location /mosquitto/data/
log_dest stdout
password_file /mosquitto/config/passwd
allow_anonymous false
```

The two lines that matter most are `allow_anonymous false`
(no open broker on your LAN) and `password_file`, which points
at the auth file you're about to create.

Now create a user. The `mosquitto_passwd` tool lives inside the
image, so run it via a one-shot container that mounts your config
directory:

```bash
docker run --rm -it -v ~/mosquitto/config:/mosquitto/config \
  eclipse-mosquitto \
  mosquitto_passwd -c /mosquitto/config/passwd wattpost
```

The `-c` flag creates a fresh password file. Drop the `-c` if
you're adding a second user later. The shell will prompt for the
password twice; pick something strong because this is now your
broker's only line of defence.

Bring the broker up:

```bash
docker run -d --name mosquitto --restart unless-stopped \
  -p 1883:1883 \
  -v ~/mosquitto/config:/mosquitto/config \
  -v ~/mosquitto/data:/mosquitto/data \
  -v ~/mosquitto/log:/mosquitto/log \
  eclipse-mosquitto
```

Verify it's listening:

```bash
docker logs mosquitto | tail -3
```

You should see `Opening ipv4 listen socket on port 1883`. If
you see anything about the password file, fix the path and
restart the container.

## Step 2: Point WattPost at the broker

On your WattPost dashboard, go to **Settings → Integrations →
MQTT**. Fill in the form:

- **Host**: the LAN IP of the host running Mosquitto.
- **Port**: `1883`.
- **Username**: `wattpost` (or whatever you set in Step 1).
- **Password**: the one you typed into `mosquitto_passwd`.
- **Base topic**: leave as the default
  (`wattpost/<appliance-id>`).

Tick **Publish to MQTT** and click **Save**. A green tick means
the broker accepted the connection. A red banner usually means
the IP is wrong, the firewall is blocking 1883, or the
credentials are off by one character.

You can confirm WattPost is publishing without involving Home
Assistant at all:

```bash
docker exec -it mosquitto mosquitto_sub \
  -h localhost -u wattpost -P 'YOUR-PASSWORD' \
  -t '#' -v | head -20
```

Within sixty seconds you should see lines like `wattpost/abc123/
state/battery_voltage_v 13.42`. If the broker is publishing but
HA isn't picking sensors up, the problem is on the HA side, not
WattPost's.

## Step 3: Add the MQTT integration to Home Assistant manually

This is the step that differs from the add-on flow. The add-on
auto-configures HA's MQTT integration for you. Without it, you
add the integration the same way you'd add any other.

In Home Assistant: **Settings → Devices & services → Add
integration → MQTT**.

Fill in the same broker details:

- **Broker**: the LAN IP of your Mosquitto host (same value you
  put into WattPost).
- **Port**: `1883`.
- **Username** / **Password**: the credentials you created. You
  can either reuse the `wattpost` user (simpler) or create a
  second user just for HA via `mosquitto_passwd` without the
  `-c` flag.
- **Advanced options**: leave **Enable discovery** ticked. Leave
  the discovery prefix as `homeassistant`.

Click **Submit**. Home Assistant will probe the broker and
report success.

## Step 4: Watch the sensors land

Once both WattPost and Home Assistant are talking to the same
broker, the flow is identical to the add-on path:

1. WattPost publishes discovery messages on `homeassistant/...`.
2. Home Assistant picks them up and creates one device per
   appliance, with one sensor per metric.
3. Each sensor lands with the right unit (`%`, `V`, `A`, `W`,
   `°C`, `Wh`) and the right device class, so the History tab
   graphs them correctly.

Go to **Settings → Devices & services → MQTT** and click into
the new device. You should see every WattPost metric as a
sensor, ready to drop into a dashboard or an automation.

## Variations

### Standalone Mosquitto on a separate host

Identical to the steps above, just on a different machine. Make
sure the host's firewall lets TCP 1883 in from both WattPost and
Home Assistant.

### Home Assistant Core (no Docker)

If HA itself runs as a venv on a Pi, install Mosquitto from your
distro's package manager (`apt install mosquitto
mosquitto-clients` on Debian / Ubuntu / Raspberry Pi OS) and
edit `/etc/mosquitto/mosquitto.conf` with the same listener /
auth / password directives as Step 1. The `mosquitto_passwd`
tool is in the same package.

### TLS

If you're publishing over the public internet (a remote Mosquitto
hosted on a VPS) you need TLS. Generate certs with
[certbot](https://certbot.eff.org/) or
[`mkcert`](https://github.com/FiloSottile/mkcert), point
Mosquitto at them via `listener 8883`, `cafile`, `certfile` and
`keyfile`, then bump WattPost's port to `8883` and tick the
**TLS** option. Use a Let's Encrypt cert if the host has a real
domain; a self-signed one works for closed networks.

## Troubleshooting

**WattPost says "connection refused".** The broker isn't
listening on the address you gave WattPost, or a firewall is in
the way. From the broker host, run `ss -tlnp | grep 1883` and
confirm Mosquitto is bound to `0.0.0.0` (not just `127.0.0.1`).
From WattPost's host, run `nc -vz <broker-ip> 1883`; if that
times out, it's a network problem, not a broker problem.

**WattPost connects but Home Assistant sees no device.** Confirm
HA's MQTT integration is pointing at the same broker IP. Use the
`mosquitto_sub` command from Step 2 to verify the discovery
messages are arriving on the broker. If they are and HA still
isn't seeing them, toggle **Enable discovery** off and on in the
MQTT integration config. HA caches the discovery payload
aggressively.

**Auth failures right after restart.** The `password_file`
permissions need to be readable by the `mosquitto` user inside
the container. `chmod 0600 ~/mosquitto/config/passwd` and
`chown 1883:1883 ~/mosquitto/config/passwd` is the standard
combo (the eclipse-mosquitto image runs as UID 1883).

## Conclusion

You now have a standalone Mosquitto broker that WattPost
publishes to and Home Assistant subscribes from, with the same
auto-discovery convenience as the add-on flow. From here, the
[low-SoC alerts guide](/blog/ha-low-soc-alerts) and the
[Energy dashboard guide](/blog/ha-energy-dashboard) work
identically, because what's downstream of the broker doesn't
care how the broker got there.
