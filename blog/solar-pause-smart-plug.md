# Pause your AC charger with a smart plug when the sun is doing the work

WattPost v0.1.21 shipped the solar-pause rule engine: when your
bank is healthy and the sun is producing, the daemon decides to
turn the AC charger off so you stop drawing from the grid or
the generator. When the bank drops too low, it turns it back on
before things get bad.

The thing it pauses isn't the charger directly. It's a smart
plug sitting between the wall socket and the charger's IEC lead.
Pulling power upstream works with any charger, any vendor, any
inverter-charger. The charger itself is unchanged, the plug just
stops feeding it.

This guide walks the setup end to end with a **Shelly Plus
Plug S** (Gen2), but Tasmota-flashed plugs work identically.

## Prerequisites

- A WattPost appliance on v0.1.22 or newer.
- A Shelly Plus Plug S (or any Shelly Gen2 switch), or a
  Tasmota-flashed plug. ~£15 for a Shelly off the shelf.
- A Wi-Fi network the plug can join.
- The plug's LAN address (we'll get this in step 2).

The plug needs to live on the same network WattPost is on. No
internet, no cloud, no Home Assistant.

## Step 1: Wire the plug between the wall and the charger

Plug it into the wall socket. Plug your AC charger's mains lead
into the smart plug. The smart plug now controls whether the
charger gets mains power.

Do this with the charger physically off first. Smart plugs are
designed to make and break full-load circuits, but you want a
clean baseline before any code touches it.

## Step 2: Onboard the plug onto your Wi-Fi

Plug it in. The Shelly creates its own open AP called
`ShellyPlugS-XXXXX` for the first 60 seconds.

Open WattPost's wizard from another device on your phone or
laptop, connect that device's Wi-Fi to the Shelly AP, then go
to `http://192.168.33.1/` in your browser. Set your house
Wi-Fi SSID and password under the Wi-Fi tab. The Shelly
reboots and joins your network.

Find its new IP in your router's connected-devices list, or
use the official Shelly app, or run `nmap -sn 192.168.1.0/24
| grep -i shelly` if you're comfortable with a terminal. Note
it down. (Optional but smart: pin a DHCP reservation on it so
the IP doesn't drift.)

Set a Shelly password under **Settings → Security** if you
haven't already. Anyone on the same network can otherwise
toggle the plug. WattPost supports basic-auth on the connection.

## Step 3: Add the plug to WattPost's config.yaml

SSH into the Pi (or `docker exec` into the container) and open
your config:

```bash
sudoedit /etc/wattpost/config.yaml
```

Add a `smart_plugs:` block:

```yaml
smart_plugs:
  - name: AC charger
    kind: shelly_gen2
    host: 192.168.1.50      # the plug's IP
    password: yourplugpass    # omit if you didn't set one
```

For Tasmota:

```yaml
smart_plugs:
  - name: AC charger
    kind: tasmota
    host: 192.168.1.51
    user: admin               # if you set web auth on tasmota
    password: yourplugpass
```

Save the file. The daemon picks the change up on its next poll
cycle. No restart required.

## Step 4: Wire the rule to the plug

Open your WattPost dashboard at `http://<your-pi>/` and go to
**Settings → Solar-aware charger pause**.

- Tick **Enabled**.
- Pick **AC charger (plug)** from the **Output to control**
  dropdown. The smart plug you just added shows up there
  automatically.
- The four thresholds are sane out of the box: pause above 80%
  SoC, resume below 50%, hard floor 30%, PV surplus 50 W.
  Tweak if your bank chemistry or load profile is unusual.
- Click **Save**.

The **Live decision** row shows what the rule is currently
doing (`force_off` / `force_on` / `unchanged`) and the reason
in plain English.

## Step 5: Watch it work

On the dashboard's Flow strip, the AC charger tile (or whatever
source tile your charger feeds into) shows **Paused, solar
covering** when the rule has pulled the plug. When the bank
drops below the recover threshold, the rule flips back, the
charger comes alive, and the tile returns to its normal V/A/W
display.

You can manually override at any time by toggling the plug
either from WattPost's output list, from the Shelly's own
local web UI, or from your phone via the Shelly app. The rule
detects the manual change and stops touching the plug until
you re-enable it.

## What you've saved

A 200 W AC charger drawing from grid for, say, six hours a day
because nobody pulled the plug after the sun came up is
1.2 kWh / day. At UK rates that's ~30 p / day. Off-grid users
running a generator save the equivalent in fuel, which adds up
faster than electricity.

The bigger win is that you stop putting the bank through
unnecessary cycles. Most LFP packs are rated for thousands of
cycles, but every cycle you don't waste is one you keep in the
bank's life budget.

## Troubleshooting

**The plug doesn't show up in the dropdown.** The daemon hasn't
re-read config yet. Wait one poll cycle (default 60 s) or
restart the appliance.

**Toggle from the dashboard works, the rule never fires.** Check
the live decision line in Settings. It explains why. Common
reasons: SoC sits between recover and target (so neither
condition triggers), or you're inside the cooldown window from a
previous change.

**"shelly write failed: HTTPError 401".** Your password
mismatch. Check it in the Shelly's own settings page.

**Plug works fine from its app but WattPost times out.** Your
firewall or VLAN is blocking port 80 between WattPost and the
plug's subnet. Move them onto the same VLAN, or open up
TCP 80 specifically between them.

## Conclusion

You've got the solar-aware pause rule controlling a smart plug
on your AC charger's mains lead, fully off-grid, no broker, no
HA, no cloud. The same plug works as the universal control
surface for anything else you wire through it. Want to pause
something else on a different rule? Add another plug entry
and another smart-scene rule in v0.1.22+.
