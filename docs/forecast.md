# PV forecast (Solcast)

WattPost pulls a 7-day PV forecast from
[Solcast](https://solcast.com/) and overlays it on:

- the **Tomorrow** tile on the dashboard (expected kWh, peak power +
  time, day-after preview, a translucent sparkline)
- the **History chart** when you're viewing `pv_power_w` (dashed amber
  line projecting into the future)

It's **opt-in**. No forecast is fetched until you supply credentials.

## Why your own key (not ours)

Solcast's terms require one key per end-user — we can't legally proxy
forecasts through a shared key. The upside: nothing about your forecast
goes through anything we operate. Your daemon talks to Solcast directly
over HTTPS.

The hobbyist tier is **free for personal residential use** — two sites
per account, 10 API calls/day, 30-min resolution, 7-day window. That's
more than enough for one or two appliances.

## Setup (~5 minutes)

1. **Register at Solcast**:
   [solcast.com/free-rooftop-solar-forecasting](https://solcast.com/free-rooftop-solar-forecasting).
   They'll ask for your panel array's:
   - **Latitude/longitude** (or postcode — they'll geocode it)
   - **Capacity** — total array size in kW (e.g. 0.4 for 4× 100 W panels)
   - **Tilt** in degrees from horizontal (often the same as your roof pitch;
     for portable / RV panels, the tilt when deployed)
   - **Azimuth** — 0° = north, 90° = east, 180° = south, 270° = west.
     Southern hemisphere users will typically face their panels toward
     the equator.

2. **Copy two values** from your Solcast account:
   - **API key** — under *Account → API key* (~36 characters)
   - **Resource ID** — under *My Sites* — the UUID of the rooftop site
     you just registered

3. **Paste into WattPost**: Settings → Integrations → *Configure*.
   - Click **Test** before saving — you'll see "✓ N forecast points,
     next peak X.X kW at HH:MM" if the credentials are valid.
   - Click **Save**.

4. **Restart the daemon** (Settings → System → *Restart daemon*) so
   the background poller picks up the new config.

The first fetch lands within a few seconds of the restart; subsequent
fetches happen every 3 hours by default.

## Poll cadence

Default: **3 hours** = 8 calls/day, comfortably under the hobbyist
limit. The poller fetches once at daemon start, then every
`poll_hours` thereafter.

You can change it in Settings, but the practical range is 3–6 hours.
Hourly polling burns through the 10/day cap by mid-morning; daily
polling means the forecast won't update as the day rolls forward.

## What gets stored

The most recent forecast is cached in WattPost's SQLite DB under the
key `forecast:pv`. The cache survives daemon restarts so the dashboard
isn't blank for the next 3 hours after a reboot — the previous fetch
is served until the next poll lands.

Disabling the integration (Settings → Integrations → Edit → *Disable*)
removes the config from `config.yaml`; old cached data lingers until
the next daemon restart, then gets dropped.

## What the numbers mean

Solcast returns three power values per 30-minute slice:

| Field             | Meaning                                          |
| ----------------- | ------------------------------------------------ |
| `pv_estimate`     | The most likely PV output (median)               |
| `pv_estimate10`   | The 10th-percentile case (cloudier than expected) |
| `pv_estimate90`   | The 90th-percentile case (clearer than expected) |

Today's UI shows the median as a dashed line on the History chart
(when viewing `pv_power_w`) and fills the area between P10 and P90
with translucent amber — a wide band means the model isn't sure
(weather front coming through, dawn/dusk transitions), a narrow band
means high confidence. The Tomorrow tile and 7-day outlook strip
both use the median for their kWh figures.

## Troubleshooting

**"401 Unauthorized" on Test** — the API key is wrong or has been
rotated in your Solcast account. Re-copy from *Account → API key* and
try again.

**"404 Not Found" on Test** — the Resource ID doesn't match this
account, or the rooftop site was deleted. Find your UUID at
your [Solcast toolkit](https://toolkit.solcast.com.au) account.

**"429 Rate-limited"** — the daemon (or some other process using the
same key) blew through 10 calls today. Wait 24 h or lower the poll
cadence.

**Forecast curve looks too low / too high vs reality** — your array
parameters in Solcast (capacity, tilt, azimuth, shading) are off.
Edit the site in your Solcast dashboard, no WattPost change needed —
the next poll picks up the new model.

**Forecast tile on the dashboard didn't appear** — Solcast returned
zero useful future points (the daemon may not have polled yet, or
the polled window doesn't cover tomorrow). Wait for the next 3-hour
poll, or use Test in Settings to force one.
