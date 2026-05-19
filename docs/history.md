# History & CSV export

WattPost stores every poll forever. But **rolls it up** so the
database stays small. The History tab can chart any device's metric
over any range.

## How long do we keep what?

| Tier | Retention | Resolution |
|---|---|---|
| Raw samples | 7 days | per poll (~60s) |
| 1-minute rollups | 30 days | 1 minute |
| 1-hour rollups | 1 year | 1 hour |
| 1-day rollups | forever | 1 day |

So a chart of "last 6 hours" reads raw samples; "last week" reads
1-min rollups; "last year" reads 1-day rollups. The Resolution cell
in the stat strip tells you which one's being shown.

## Picking a range

The buttons (1h / 6h / 24h / 7d / 30d) auto-pick the right rollup
table + bucket. **Custom** opens a from/to date picker. Bucket is
computed to keep the chart around 300 points so it stays readable
even over a year.

## CSV export

The **CSV** button next to the range buttons downloads the
currently-visible chart's data:

```
timestamp,epoch,value,min,max
2026-05-13T10:52:00+0000,1778669520,88.85,88.85,88.85
2026-05-13T10:53:00+0000,1778669580,88.89,88.89,88.89
…
```

- **timestamp**: ISO-8601 with timezone. Excel / Numbers parse it.
- **epoch**: Unix seconds for arithmetic.
- **value**: the metric.
- **min, max**: present when the underlying rollup has them (1-min
  and longer). Raw samples don't have a band. Just one value.

Filename pattern: `<label>_<metric>_<since>_<until>.csv`.

## Programmatic access

Same data is on the REST API:

- `GET /api/devices/<label>/history?metric=…&since=…&until=…&bucket=…`
  → JSON.
- `GET /api/devices/<label>/history.csv?metric=…` (same params)
  → CSV stream.
- `GET /api/devices` → list of devices + their latest readings.
- `GET /api/devices/<label>/lifetime` → coulomb-counted Ah-in / Ah-out
  + equivalent cycle count (smart batteries only).
- `GET /api/load_heatmap?days=30` → hour-of-day × day-of-week grid of
  average load wattage.
