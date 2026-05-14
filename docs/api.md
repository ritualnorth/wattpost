# REST API reference

WattPost exposes a public REST API at `https://app.wattpost.io/api/v1/*` for paid-tier users. Read-only in v1, JSON, bearer-token auth.

Use cases:

- Pull telemetry into [Home Assistant](#home-assistant-example) / [Grafana](#grafana-example) / [Google Sheets](#sheets-example)
- Build custom dashboards that combine multiple sites
- Mobile apps + automation tools (n8n, Zapier-style)

## Authentication

Mint a token from **[/app/account/api-keys](/app/account/api-keys)** on the dashboard. The token is shown **once at creation**; we store only an argon2 hash. Lose it → revoke and mint a new one.

Send it with every request:

```
Authorization: Bearer wp_a3f9c12d4e5b6789abcdef0123456789ab
```

| HTTP | Meaning                                                  |
| ---- | -------------------------------------------------------- |
| 401  | Missing / malformed / revoked / unknown token            |
| 403  | Token valid but the account isn't on a paid plan         |
| 429  | Rate-limit exceeded                                      |

## Endpoints

### `GET /api/v1/me`

Account info + quota.

```json
{
  "email": "you@example.com",
  "plan": "cloud",
  "created_at": "2026-05-12T18:14:23Z",
  "rate_limit": {
    "per_month_limit": 10000,
    "per_month_used": 158,
    "per_month_resets_at": "2026-06-01"
  }
}
```

### `GET /api/v1/sites`

Every appliance the token's owner has paired.

```json
{
  "sites": [
    {
      "id": 42,
      "label": "RV — rooftop",
      "slug": "ax4mn3uo01",
      "tunnel_url": "https://ax4mn3uo01.wattpost.io/",
      "online": true,
      "last_seen": "2026-05-14T13:47:01Z",
      "created_at": "2026-05-12T18:30:00Z"
    }
  ]
}
```

### `GET /api/v1/sites/{id}`

Latest snapshot for one site.

```json
{
  "id": 42,
  "label": "RV — rooftop",
  "online": true,
  "last_seen": "2026-05-14T13:47:01Z",
  "latest": {
    "received_at": "2026-05-14T13:47:01Z",
    "soc_pct": 87.3,
    "net_w": 142.0,
    "extras": {
      "version": "0.0.1",
      "alert_count": 0,
      "pv_today_wh": 4218,
      "load_today_wh": 1840
    }
  }
}
```

### `GET /api/v1/sites/{id}/extras`

Just the parsed `extras_json` from the latest heartbeat. Cheaper if you only need `pv_today_wh` / `load_today_wh` / `alert_count`.

### `GET /api/v1/sites/{id}/history`

Heartbeat time series.

| Param         | Default     | Notes                                  |
| ------------- | ----------- | -------------------------------------- |
| `from_`       | 24h ago     | ISO timestamp                          |
| `to`          | now         | ISO timestamp, must be > from          |
| `granularity` | `auto`      | `raw` / `5min` / `hour` / `auto`       |

Max 1000 points per response, 90-day range cap.

```json
{
  "site_id": 42,
  "from": "2026-05-13T13:47:01Z",
  "to":   "2026-05-14T13:47:01Z",
  "granularity": "5min",
  "points": [
    { "received_at": "2026-05-14T13:45:00Z", "soc_pct": 87.3, "net_w": 142.0 }
  ]
}
```

## Examples

### curl

```bash
TOKEN=wp_xxx
curl -sf -H "Authorization: Bearer $TOKEN" \
  https://app.wattpost.io/api/v1/sites | jq .sites[]
```

### Python

```python
import httpx
TOKEN = "wp_xxx"
r = httpx.get(
    "https://app.wattpost.io/api/v1/sites",
    headers={"Authorization": f"Bearer {TOKEN}"},
    timeout=10,
)
r.raise_for_status()
for s in r.json()["sites"]:
    print(s["label"], s["online"])
```

### Home Assistant

`configuration.yaml`:

```yaml
sensor:
  - platform: rest
    name: rv_soc
    resource: https://app.wattpost.io/api/v1/sites/42
    headers:
      Authorization: Bearer wp_xxx
    value_template: "{{ value_json.latest.soc_pct }}"
    unit_of_measurement: "%"
    scan_interval: 300
```

### Grafana

Use the [JSON API datasource](https://grafana.github.io/grafana-json-datasource/) → URL `https://app.wattpost.io/api/v1/sites/42/history` with the bearer token as a custom HTTP header.

### Google Sheets

`=IMPORTDATA("https://app.wattpost.io/api/v1/sites/42")` doesn't pass auth headers — wrap with [an Apps Script](https://developers.google.com/apps-script/reference/url-fetch/url-fetch-app) that adds `Authorization`.

## Coming in v2

- Webhooks (outbound HMAC-signed events: alert_fired, appliance_offline)
- Live streaming over SSE
- Per-token rate limit enforcement (currently reported but not enforced)

Have ideas or hit a problem? Email [support@wattpost.io](mailto:support@wattpost.io) — the v2 backlog is operator-driven and we'd rather build what you need than guess.
