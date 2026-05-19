# What shipped in WattPost v0.1.21

A bigger batch than 0.1.20 even though only nine days separate
them. Two new features, one round of internal plumbing for the
dashboard, and a coverage roadmap doc that finally writes down
which devices come next and why.

## Reset to defaults: full TUI parity for Docker (#138)

The Pi install has had a `wattpost-config` whiptail TUI since
day one. Docker installs never did because Docker containers
aren't the right place for an interactive shell tool. The
TUI's eleven menu items have been migrating into the dashboard
piece by piece (diagnostics download, web-password rotate,
update check). The last one missing was the nuclear option:
reset everything to first-boot state.

That now lives at the bottom of Settings → Diagnostics under a
red Danger zone block. Type `RESET` into the input to enable
the button. The wipe:

- Clears every transport, device, exporter, alert, output
  schedule and smart-scene rule.
- Keeps your web password, SQLite history, branding, and (by
  default) cloud pairing. Tick "stay paired with the cloud"
  off to nuke that too.
- Writes a `config.yaml.bak` before touching disk so the
  previous state is recoverable.

After the wipe the setup wizard reopens automatically, so
re-pairing your devices is the same flow as a fresh install.

## Solar-aware AC charger pause (#163)

This is the one I'm proudest of. Off-grid setups that have both
solar and an AC charger (shore power in a van, grid in a cabin,
a generator anywhere) waste energy when both are running into a
healthy bank. WattPost can now pause the AC charger when the
sun is doing the work and wake it back up before the bank
drops too low.

The rule lives in Settings → Solar-aware charger pause. It has
four numbers that matter:

- **Pause above SoC**. Default 80%. The bank is healthy.
- **Resume below SoC**. Default 50%. Wake the charger back up
  if we drift below this.
- **Hard floor**. Default 30%. If SoC drops here regardless of
  weather forecast, the charger comes back on no matter what.
  This beats every other gate.
- **PV surplus threshold**. Default 50 W. The PV array has to
  be producing at least this much actual wattage right now
  before we pause. Stops the rule from triggering at twilight
  when the panels read "yes but only 12 W".

A 30-minute cooldown sits between any two state changes so a
passing cloud doesn't flap the relay. And if you manually
toggle the charger yourself, the rule stops touching it.
Whatever you just did, you knew something we didn't.

This is a Pro tier feature and it's off by default. The
**control point is a smart plug upstream of the charger**, not
the charger itself. We don't write to charger Modbus registers
for the AC-charger side (Renogy doesn't publish a verified
write map, and Victron is read-only by design). The output
adapter ships in **v0.1.22** with first-class support for
Shelly Plug S Gen2 and Tasmota-flashed plugs over local HTTP,
no MQTT broker or Home Assistant required. Both work fully
off-grid on your LAN.

## Hero / Flow snapshot lock (#162)

A dashboard fix that should never have shipped broken in the
first place. Three independent renderers (the SoC hero, the
Flow strip, and the alerts panel) each computed their own
freshness floor against a freshly-read `Date.now()` to decide
which device readings counted as "live". On the 90-second
staleness boundary a battery could be counted by one renderer
and excluded by another. The dashboard would visibly disagree
with itself: the hero showed "charging 50 W", the flow strip
showed the source as "silent".

The fix stamps one `nowSec` per frame and memoises the
aggregated bank + flow model so every consumer in the frame
sees the same view of the world. There's also a new
`/api/snapshot` endpoint that returns devices + poll_run +
today atomically read from the store, so the polling fallback
(used through the cloud broker on iOS Safari, where SSE is
disabled) can't straddle a poll cycle the way three
concurrent fetches could.

## Coverage roadmap (#119)

A strategic doc rather than a customer-facing one, but worth
mentioning because it shapes what the next four months of
driver work looks like. The queue is tiered by paying-persona
impact, not alphabetical:

- **Tier 1**: JBD / Overkill Solar BMS, Daly BMS, EPEVER MPPT.
  The "this is what's inside most cheap LFP packs and DIY
  builds" set.
- **Tier 2**: AiLi shunt, Junctek shunt, Battle Born / LiTime
  LFP via the underlying BMS.
- **Tier 3**: MPP Solar / Voltronic clones, Sterling Power
  DC-DC, REDARC BCDC.
- **Tier 4**: the long tail.

Plus the explicit out-of-scope list (Victron VE.Bus / Cerbo,
EcoFlow proprietary cloud, Schneider XW Pro). If you've got
hardware on the list and you're willing to plug it into a
test rig for a week, email support@wattpost.io and we'll bump
it.

## Bonus blog post: external Mosquitto broker (#195)

For Home Assistant Container, HA Core, and anyone with a
standalone Mosquitto broker already serving the rest of their
house. The five-minute HA guide assumed the add-on. This new
post walks the full eclipse-mosquitto Docker setup +
mosquitto_passwd auth + the manual HA integration flow.

## Get it

Existing Pi installs see the "Update available" badge within
their next poll cycle and can apply it via Settings → About →
Update now. Docker users: `docker compose pull && docker
compose up -d`. Fresh installs: the
[download page](https://wattpost.io/download) serves the
updated SD-card image.
