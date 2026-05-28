# How to set up WattPost on a Raspberry Pi from scratch

WattPost is a self-hosted dashboard for off-grid solar. It runs on a
Raspberry Pi, talks Bluetooth to your Renogy, Victron, and JK BMS
gear, and shows you live state of charge, power flow, and history
without a vendor cloud account. This guide takes a blank SD card to
a live dashboard in one sitting.

You will flash a Raspberry Pi, boot it up, sign in to the local
web UI, run the setup wizard to pair your first device, and watch
the first reading land on the dashboard. Optionally you will pair
to wattpost.cloud at the end for remote access from your phone.

## Prerequisites

You need the following physical items:

- A **Raspberry Pi 4 or 5** with an official USB-C power supply.
  Any RAM size works. A cheap third-party charger will undervolt
  the board and break Bluetooth in subtle ways, so use the
  official PSU.
- A **microSD card**, 16 GB or larger, A1-rated (faster random
  writes for SQLite).
- A **way to talk to your gear**. For Renogy: a BT-1 or BT-2
  dongle plugged into the charge controller's RS232 port, or a
  USB-RS485 cable wired into the same RJ45 port if you would
  rather skip Bluetooth entirely. For Victron and JK BMS,
  nothing extra. They advertise directly over BLE and the Pi's
  built-in radio picks them up.
- A **laptop or desktop** with an SD card slot (or a USB SD
  reader) to flash the image.
- The Pi and your laptop on the **same Wi-Fi or LAN** so you
  can open the dashboard once it boots.

About 15 minutes of attention. Roughly 5 of those are watching
progress bars.

## Step 1: Download the SD card image

Open [wattpost.cloud/download](https://wattpost.cloud/download)
on your laptop. The page detects the latest release and shows
the direct download link for the `.img.xz` file. Click it. The
file is around 800 MB compressed; on a normal home connection it
takes a few minutes.

<img src="/static/img/blog/first-install/01-download-page.png"
     alt="WattPost download page showing the latest SD card image with size and checksum">

If you want to verify the download against tampering, the same
page links a `SHA256` file. On macOS or Linux:

```bash
shasum -a 256 wattpost-*.img.xz
```

Compare to the published value. They should match exactly. If
they do not, redownload before flashing.

## Step 2: Flash the SD card with Raspberry Pi Imager

Install **Raspberry Pi Imager** from
[raspberrypi.com/software](https://www.raspberrypi.com/software/)
if you do not already have it. It is the official tool, free, on
macOS, Windows, and Linux.

Open Raspberry Pi Imager.

1. Click **Choose OS** → scroll to the bottom → **Use custom**.
   Point it at the `wattpost-*.img.xz` you just downloaded.
2. Click **Choose Storage** and select your SD card. Imager
   shows the card's vendor and capacity so you do not flash the
   wrong drive.
3. Click **Write**. Imager asks once to confirm because flashing
   wipes the card. Confirm. The write takes around 3–5 minutes
   on a normal A1 card.
4. When verification finishes Imager tells you it is safe to
   eject. Eject and pull the card.

There is no Wi-Fi setup or password screen to fill in. The
WattPost image handles all of that on first boot.

## Step 3: First boot

Pop the SD card into the Pi. Plug in your Bluetooth dongle (if
you are using one) or your USB-RS485 cable (if you went wired).
Plug in the USB-C power last. The Pi boots immediately.

The first boot does some one-time setup: expanding the filesystem
to fill the SD card, generating SSH keys, and starting the
WattPost daemon. Give it about 60 seconds. The green activity
LED will be busy throughout. When it goes quiet, the dashboard
is up.

If you have a monitor plugged into the Pi over HDMI you will see
the boot messages and then a banner showing the local URL and
the first-boot password. If you are running headless, do not
worry; both of those are also available over SSH and from the
dashboard's first-time sign-in screen.

## Step 4: Find the appliance on your LAN

Open your laptop's browser and go to:

```
http://wattpost.local:8000
```

That is mDNS / Bonjour resolving the Pi's hostname on your LAN.
It works on every modern router out of the box.

If your router or OS does not handle mDNS (some corporate
networks strip it), find the Pi's IP address by checking your
router's connected-devices list for a host named `wattpost` and
use that IP directly: `http://192.168.x.x:8000`. The local
network is the only place that URL responds; nothing on it is
exposed to the internet.

## Step 5: Sign in with the first-boot password

The first time you visit the dashboard, the appliance asks for a
password. It is a random `wattpost-<5 hex>` string generated on
first boot, printed on the HDMI banner, available via SSH (`cat
/etc/wattpost/web-password`), and shown in the MOTD on every SSH
login until you delete it.

<img src="/static/img/blog/first-install/05-login.png"
     alt="WattPost login screen prompting for the first-boot password">

Paste it in, sign in, and the dashboard loads.

You can rotate the password any time by SSHing in, running
`wattpost-config`, and picking the **Set / reset web password**
option from the menu.

## Step 6: Pair your first device

The dashboard greets a fresh install with a setup wizard. The
goal of this step is to get one piece of hardware reporting
live to the dashboard so you can confirm everything works
before adding more.

Open **Settings → Devices & setup**. The wizard walks you
through three numbered steps.

**Bluetooth connection.** Pick which transport your hardware
uses. If you plugged in a Renogy BT-1 or BT-2 dongle, choose
**Renogy BT-2 dongle**. If you went wired with a USB-RS485
cable, choose **USB-RS485 wired**. For Victron or JK BMS gear
the dashboard reads BLE advertisements directly, so you skip
this step.

**Scan for devices.** Click **Scan**. WattPost watches for 30
seconds and lists every device it can read. Renogy gear appears
as `BT-TH-...` strings, Victron as their friendly product names
(SmartShunt, SmartSolar 100/30, Orion XS), JK BMS modules as
`JK-Bx...` IDs.

<video src="/static/img/video-devices.webm" autoplay muted loop playsinline></video>

**Confirm and save.** Pick the device you want to pair. Give
it a friendly label like "main controller" or "garage shunt".
Save. The wizard closes and a card for the new device appears
on the dashboard.

If the scan finds nothing, the wizard tells you why specifically
(no BLE adapter, adapter present but no advertisements, scanner
blocked by another process). Each error has a link to the right
troubleshooting page in the docs.

## Step 7: Watch the first reading land

WattPost polls each paired device on its own cadence (Renogy
every 10 seconds over BT-2; Victron whenever an advertisement
hits the radio, usually every 1–3 seconds; JK BMS the same).

Within seconds of confirming the pair, the dashboard fills in.

<video src="/static/img/video-dashboard.webm" autoplay muted loop playsinline></video>

The big donut on the left is **state of charge** for your
battery bank. The strip below it shows live **power flow**: how
many watts are coming in from solar, going out to loads, and the
net direction the battery is moving. The tiles to the right
track today's energy in and out, the runtime forecast if your
SoC keeps trending the way it is, and any alerts you have set.

This is the dashboard. Bookmark it. The URL is stable; nothing
about it depends on the cloud.

## Step 8: Add the dashboard to your phone

The dashboard is a Progressive Web App. On a phone you can pin
it to the home screen and it opens as a full-screen app with
no browser chrome, like a native install.

**On iOS Safari:** open `http://wattpost.local:8000` (your
LAN URL), tap the **Share** button, scroll down to **Add to
Home Screen**, tap **Add**. The WattPost icon appears on your
home screen. Tap it to open.

**On Android Chrome:** open the same URL, tap the three-dot
menu, tap **Add to Home Screen**. Same outcome.

<video src="/static/img/video-mobile.webm" autoplay muted loop playsinline></video>

The mobile layout is the same dashboard, rearranged for a tall
narrow screen. The same data, the same controls.

## Step 9: Pair to wattpost.cloud (optional)

Everything you have done so far works without any internet
account. The local appliance polls, stores history, drives
alerts via ntfy or MQTT, and shows the dashboard on your LAN.
You can stop here and never touch the cloud.

If you want **remote access** from outside your home network,
**off-site backups**, **push notifications** to your phone, or
**multi-site fleet view**, pair the appliance to
[wattpost.cloud](https://wattpost.cloud).

1. Open **Settings → Integrations → WattPost cloud** on the
   appliance dashboard.
2. Click **Generate pairing code**. A 6-character code appears
   with a 10-minute expiry.
3. In another browser tab, go to
   [wattpost.cloud/signup](https://wattpost.cloud/signup),
   create an account, and pick the **+** button on the empty
   dashboard.
4. Paste the pairing code. The appliance and the cloud handshake
   over the next heartbeat. The new appliance card appears on
   your cloud dashboard within a few seconds.

A Cloudflare tunnel is provisioned automatically when pairing
succeeds. The **Open** button on the cloud dashboard opens the
local dashboard remotely, through your cloud session, with no
extra login or port forwarding.

Pairing starts a 14-day free trial. After that, WattPost Cloud is
£6/month. The local appliance keeps working forever, with or without
a paid plan.

## Conclusion

You have a Raspberry Pi running WattPost, a paired device
reporting live, a dashboard on your laptop and phone, and
optionally a cloud account watching the appliance from outside
your network.

For everything else, the docs are the canonical reference:

- [Supported hardware](/docs/supported-hardware): the full
  list of charge controllers, batteries, shunts, and BMSes
  WattPost reads today.
- [Alerts](/docs/alerts): wire up ntfy, Discord, email, MQTT,
  webhook, or Pushover to fire when SoC drops below a
  threshold.
- [Home Assistant integration](/docs/mqtt): expose every
  metric as an auto-discovered HA sensor over MQTT.
- [Wired setup](/docs/wired-setup): swap the BT-2 dongle for
  a USB-RS485 cable for immunity from Bluetooth interference.
- [Kiosk mode](/docs/vanlife-kiosk): turn a 7-inch Pi touch
  display into a wall-mounted dashboard for a van or cabin.

If you got stuck anywhere, email
[support@wattpost.io](mailto:support@wattpost.io). One of the
team reads every message and replies within a day.
