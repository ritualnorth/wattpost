# SaaS REST API. V1 spec (planning)

Status: **not yet implemented**. This doc is the design we agreed on before
building. Cross-references the cloud dashboard's existing `/api/*` endpoints
(those are internal/UI-only. Not part of the public API).

The public API lives under `/api/v1/*` to keep it isolated from the dashboard's
internal `/api/*` paths.

## Goals

1. Let cloud-tier users pull WattPost telemetry into their own systems ·
   Grafana, Home Assistant, Node-RED, custom scripts, Google Sheets.
2. Work as a paid-tier upsell. Gated behind the Cloud plan, with quota
   tiers a natural pricing knob.
3. Cheap to build and maintain: read-only first, no webhooks, no streaming
   in v1.

## Out of scope for v1

- Write operations (no remote control of appliances; do that locally)
- Webhooks (v2; spec'd in this doc's "Future" section)
- gRPC / WebSocket / SSE streaming (v3 if ever)
- Per-site granular permissions (v1 = "your token can see all your sites")
- OAuth2 for third-party integrations (v3+)

## Authentication

**Bearer-token, no sessions.** API tokens are issued from
`/app/account/api-keys` on the dashboard.

```
Authorization: Bearer wp_<32-hex-chars>
```

Token format: `wp_` prefix + 32 random hex chars (16 bytes of entropy).
Prefix lets users grep their secrets for the format and lets us
parse-and-reject expired-style tokens quickly. Stored hashed in the DB
(argon2id, same as user passwords).

Token shown to the user **once** at creation time. After that the dashboard
shows only the first 6 chars + last 4 (`wp_a3f9…c41d`) plus `last_used_at`
and the label. No way to retrieve the full secret. Lose it, create a new one.

### Auth response codes

| HTTP | Meaning                                                  |
| ---- | -------------------------------------------------------- |
| 401  | Missing / malformed token                                |
| 401  | Token revoked or not recognised                          |
| 403  | Token valid but the account isn't on a paid plan         |
| 429  | Rate-limit exceeded (see rate-limit headers)             |

## Versioning

URL-prefixed (`/api/v1/`). Major version = breaking-change boundary. Once
v1 is announced GA, we commit to:

- Never remove a field from a response
- Never rename a field
- Never change the semantic meaning of a field
- Adding new fields is fine and shouldn't break clients
- Deprecation cycle: a field's deprecation is announced in the changelog
  + the response header `Sunset: <ISO date>` for at least 6 months before
  removal in a future version

`/api/v0/` is reserved for the pre-GA period. Anything we want to ship
to early adopters with no compat guarantees.

## Endpoints

### `GET /api/v1/me`

Account info + plan + quota status.

**Response 200:**

```json
{
  "email": "you@example.com",
  "plan": "cloud",
  "created_at": "2026-05-12T18:14:23Z",
  "rate_limit": {
    "per_minute": 60,
    "per_month_remaining": 9842,
    "per_month_resets_at": "2026-06-01T00:00:00Z"
  }
}
```

### `GET /api/v1/sites`

List every appliance the token's owner has paired.

**Response 200:**

```json
{
  "sites": [
    {
      "id": 42,
      "label": "RV rooftop",
      "slug": "ax4mn3uo01",
      "tunnel_url": "https://ax4mn3uo01.wattpost.io/",
      "online": true,
      "last_seen": "2026-05-14T13:47:01Z",
      "version": "0.0.5"
    }
  ]
}
```

`tunnel_url` is null for appliances paired before tunnel provisioning was
configured cloud-side. `version` comes from the appliance's heartbeat
extras.

### `GET /api/v1/sites/{id}`

Current snapshot of one site. The data behind the dashboard card.

**Response 200:**

```json
{
  "id": 42,
  "label": "RV rooftop",
  "online": true,
  "last_seen": "2026-05-14T13:47:01Z",
  "latest": {
    "received_at": "2026-05-14T13:47:01Z",
    "soc_pct": 87.3,
    "net_w": 142.0,
    "extras": {
      "version": "0.0.5",
      "alert_count": 0,
      "pv_today_wh": 4218,
      "load_today_wh": 1840
    }
  }
}
```

`extras` is whatever the appliance shipped in its last heartbeat. The
shape is fixed by the appliance side; this API doesn't transform or
validate it beyond what the heartbeat ingest already does.

**404** if the site exists but belongs to another account.

### `GET /api/v1/sites/{id}/history`

Historical heartbeat series.

**Query params:**

| Param         | Type    | Default            | Range                              |
| ------------- | ------- | ------------------ | ---------------------------------- |
| `from`        | ISO ts  | 24h ago            | Up to 90 days back                 |
| `to`          | ISO ts  | now                | Must be > `from`                   |
| `granularity` | string  | `auto`             | `raw` / `5min` / `hour` / `auto`   |

`auto` picks `raw` for ≤2h ranges, `5min` for ≤7d, `hour` for longer.
Raw points are exactly what the appliance sent at its heartbeat cadence
(typically every 5 min). 5min and hour are server-side averages.

**Response 200:**

```json
{
  "site_id": 42,
  "from": "2026-05-13T13:47:01Z",
  "to":   "2026-05-14T13:47:01Z",
  "granularity": "5min",
  "points": [
    {"ts": "2026-05-14T13:45:00Z", "soc_pct": 87.3, "net_w": 142.0},
    {"ts": "2026-05-14T13:40:00Z", "soc_pct": 87.1, "net_w": 140.0}
  ]
}
```

Points are newest-first, max 1000 per response. If the range needs more,
clients paginate via `from`/`to` adjustments. No cursor pagination in v1.

**400** if `from > to`, range > 90 days, or granularity unknown.

### `GET /api/v1/sites/{id}/extras`

Just the latest `extras_json` blob, parsed. Useful for clients that want
to read fields the API doesn't dedicated-endpoint yet (`pv_today_wh`,
`load_today_wh`, `alert_count`, future custom keys).

**Response 200:**

```json
{
  "site_id": 42,
  "received_at": "2026-05-14T13:47:01Z",
  "extras": {
    "version": "0.0.5",
    "alert_count": 0,
    "pv_today_wh": 4218,
    "load_today_wh": 1840
  }
}
```

## Rate limiting

Two limits per token:

- **Per-minute**: token-bucket, refilled continuously, default 60/min.
- **Per-month**: hard cap, resets at the start of each calendar month UTC.
  Cloud plan default: 10,000/month.

Both limits are reported in every response via headers:

```
X-RateLimit-Limit-Minute: 60
X-RateLimit-Remaining-Minute: 54
X-RateLimit-Limit-Month: 10000
X-RateLimit-Remaining-Month: 9842
X-RateLimit-Reset-Month: 2026-06-01T00:00:00Z
```

429 response when either limit is hit, with `Retry-After: <seconds>`.

Implementation: in-memory token bucket per token id, persisted-counter
in Postgres for the monthly cap. Switch to Redis if we ever scale past
one cloud container.

## API keys UI

Lives at `/app/account/api-keys`. New row in the sidebar (or sub-section
on the existing Account page).

Each key shows:

- Label (user-chosen, max 64 chars)
- Prefix (`wp_a3f9…`)
- Last used (timestamp + IP)
- Created at
- Actions: Revoke (immediate; cached requests inflight may complete)

"Create key" opens a modal that takes a label, returns the token. The
modal stays open with a big copy-to-clipboard box and a "Copy then close"
button. Closing the modal hides the full token forever.

## Database

One new table:

```sql
CREATE TABLE api_tokens (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_prefix    VARCHAR(16) NOT NULL,        -- "wp_a3f9c12d"
    token_hash      VARCHAR(255) NOT NULL,        -- argon2id digest
    label           VARCHAR(64) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at      TIMESTAMPTZ NULL,
    last_used_at    TIMESTAMPTZ NULL,
    last_used_ip    INET NULL,
    monthly_usage   INTEGER NOT NULL DEFAULT 0,
    monthly_reset   DATE NOT NULL DEFAULT (DATE_TRUNC('month', NOW()) + INTERVAL '1 month')::DATE
);

CREATE UNIQUE INDEX api_tokens_prefix_idx ON api_tokens (token_prefix) WHERE revoked_at IS NULL;
CREATE INDEX api_tokens_user_idx ON api_tokens (user_id) WHERE revoked_at IS NULL;
```

`token_prefix` is unique among active tokens so the auth middleware can
do `WHERE token_prefix = ? AND revoked_at IS NULL` to short-circuit
before the argon2 verify (which is intentionally slow).

## Pricing tier integration

```
┌─── Local ──────┐   ┌─── Cloud ─────────────┐
│                │   │                       │
│ Free forever   │   │ Everything in Local + │
│                │   │   Multi-site dash     │
│ (no API)       │   │   Offline alerts      │
│                │   │   Remote tunnel       │
│                │   │ ▸ REST API access     │
│                │   │   Cross-site rules    │
└────────────────┘   └───────────────────────┘
```

Free-plan users hitting any `/api/v1/*` get a clear 403:

```json
{
  "error": "api_not_in_plan",
  "message": "REST API access requires the Cloud plan. Upgrade at https://wattpost.cloud/account/billing.",
  "upgrade_url": "https://wattpost.cloud/account/billing"
}
```

## Documentation

`/api/docs` on `wattpost.cloud`, a single markdown-rendered page. Examples
in `curl`, Python (`httpx`), and a Home Assistant `rest:` sensor config
for each endpoint. OpenAPI spec auto-generated by Litestar. Link to it
at the top of the docs page but don't make it the primary surface.

## Implementation order

1. `api_tokens` table + alembic migration
2. Auth middleware (`Authorization: Bearer wp_…`) plus the
   "plan gate" check
3. `/app/account/api-keys` page (create, list, revoke)
4. `GET /api/v1/me`
5. `GET /api/v1/sites` + `/sites/{id}` + `/sites/{id}/extras`
6. `GET /api/v1/sites/{id}/history` (the heaviest endpoint; needs proper
   query planning + the downsampling code)
7. Rate limiter
8. Docs page + OpenAPI link
9. Pricing-page update + plan gate

Each of 1-7 is independently shippable (behind feature flag) so we can
land it in stages.

## Future (v2 / v3)

**Webhooks (v2):**

User defines a webhook URL + secret on the dashboard. Cloud POSTs
JSON events to it for: alert_fired, alert_cleared, appliance_offline,
appliance_online, daily_summary. HMAC-SHA256 signature in
`X-WattPost-Signature`. Delivery retries: exponential backoff up to
5 attempts over 1h; after that the event is dropped + the webhook
is auto-disabled until the user re-enables.

**Streaming (v3 if at all):**

`GET /api/v3/sites/{id}/stream` as an SSE feed of live heartbeats.
Useful for real-time dashboards but heavy on the cloud. Only worth
building if there's demand. Otherwise people poll `/api/v1/sites/{id}`
every 5-15 min and it's fine.

**Write actions (v3+):**

`POST /api/v3/sites/{id}/poll` to force an immediate poll. Other
write operations are deliberately out of scope. The local appliance
is the source of truth for config; cloud doesn't push state down.

## Related

- [docs/architecture.md](architecture.md). Appliance internals
- `cloud/wattpost_cloud/api/heartbeat.py`. Ingest, source of the data this API exposes
- `cloud/wattpost_cloud/api/sites.py`. Dashboard's internal sites endpoint; the v1 API will share most of its logic
