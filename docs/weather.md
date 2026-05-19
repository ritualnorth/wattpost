# Current weather (Open-Meteo)

The dashboard's **Right now** tile shows live conditions for your
site. Temperature, cloud cover, wind, humidity, and sunrise / sunset
times. Polled from [Open-Meteo](https://open-meteo.com/), a free
public weather API.

**No API key required.** Open-Meteo's hobbyist endpoint is free for
personal use with generous rate limits (~10k calls/day). All you need
is your latitude and longitude.

This is separate from the [PV forecast (Solcast)](#/docs/forecast) ·
they answer different questions:

- **Solcast** → *What PV output should I expect over the next few days?*
- **Open-Meteo** → *What's actually happening outside right now?*

Together they tell you whether you're getting the PV the model
predicted, why not when you're not (clouds rolled through), and
whether you should plan for a thin day tomorrow (cloud cover going
up overnight).

## Setup (~2 minutes)

1. **Find your lat/lon.** If you already wired up Solcast, you used
   coordinates when registering your rooftop site. Use the same.
   Otherwise:
   - Open [maps.google.com](https://maps.google.com), find your site,
     right-click → the coordinates show in the popover.
   - Or [latlong.net](https://www.latlong.net/). Paste your postcode,
     copy the result.

2. **Paste into WattPost**: Settings → Integrations → **Open-Meteo
   weather → Configure**.
   - Enter latitude and longitude (decimal degrees, e.g. `51.5074`
     and `-0.1278` for central London).
   - Leave poll cadence at the default (15 min) unless you have a
     reason to change it.
   - Click **Test**. You'll see "✓ 11°C · 99% cloud" or similar.
   - Click **Save**.

3. **Restart the daemon** so the background poller picks up the new
   config (Settings → System → *Restart daemon*).

The first fetch lands within a few seconds of the restart; subsequent
fetches happen every 15 min by default.

## What's on the tile

| Field        | Meaning                                              |
| ------------ | ---------------------------------------------------- |
| Temperature  | Air temperature at 2 m, °C                           |
| Conditions   | Sunny / partly cloudy / drizzle / etc.. WMO code    |
| Cloud cover  | 0–100 %. Feeds into "why's my PV low?"              |
| Wind         | Speed in m/s + compass direction                     |
| Humidity     | Relative humidity, %                                 |
| Sunrise      | Today's local sunrise time                           |
| Sunset       | Today's local sunset time                            |

The icon next to the temperature follows the WMO weather code ·
sun for clear, partly-clouded sun for 1–2 oktas, cloud for overcast,
drops for rain, snowflakes for snow, etc.

## What gets stored

The most recent fetch is cached in WattPost's SQLite DB at key
`weather:current`. Cache survives daemon restarts so the tile isn't
blank for 15 minutes after a reboot. The previous fetch is served
until the next poll lands.

Disabling the integration (Settings → Integrations → Edit → *Disable*)
removes the config from `config.yaml`; cached data clears on the
next daemon restart.

## Privacy note

Open-Meteo's free endpoint receives your latitude/longitude in every
request. There's no API key tying these requests to an account, but
your coordinates are visible to Open-Meteo (and anyone on the path
between your appliance and them) on each poll.

If that's a concern: skip this integration. The PV forecast (Solcast)
sends the same coordinates implicitly via your registered site, but
goes through a key bound to your Solcast account; current weather
adds a second outbound HTTPS call. WattPost still functions without
either, fully offline.

## Troubleshooting

**"Open-Meteo rejected the request" on Test**. Coordinates are
outside [-90,90] / [-180,180]. Double-check you entered decimal
degrees, not degrees/minutes/seconds.

**Tile says "Open-Meteo · refreshed. Ago"**. The daemon hasn't
fetched yet. Wait one poll cycle (15 min default) or restart.

**Temperature looks off vs reality**. Open-Meteo blends several
weather models and interpolates between station observations. A
couple of degrees of mismatch with an outdoor thermometer is normal.
For appliance use the WMO conditions code matters more than the
exact temperature.
