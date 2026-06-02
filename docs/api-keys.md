# API keys

WattPost API tokens are minted at **[/app/account/api-keys](https://wattpost.cloud/app/account/api-keys)** while signed into the cloud dashboard.

## Creating a token

1. Sign in at [wattpost.cloud](https://wattpost.cloud)
2. **Account → Manage API keys → Create key**
3. Give it a label so you can identify it later (e.g. "Home Assistant", "Grafana on rpi5", "personal scripts")
4. The cleartext token is shown **once**. Copy it into your password manager / app config now. We store only an argon2 hash and cannot recover it.

## Format

Tokens look like `wp_a3f9c12d4e5b6789abcdef0123456789ab`:

- `wp_` prefix. Grep-friendly
- 32 hex chars of entropy

The dashboard's API-keys list shows the prefix (`wp_a3f9c12d…`) plus the label, creation time, last-used time, and last-used IP. The cleartext never reappears.

## Using a token

Pass it as a Bearer token on every `/api/v1/*` request:

```
Authorization: Bearer wp_a3f9c12d4e5b6789abcdef0123456789ab
```

See [API reference](/docs/api) for endpoint shapes.

## Rotating + revoking

- **Compromised?** Revoke immediately from the keys list. The token starts returning 401 within seconds.
- **Routine rotation:** mint a new one, swap it into your client, then revoke the old. Tokens have no built-in expiry. They're valid until revoked.
- **Lost the cleartext?** No recovery path. Revoke and mint a new one.

## Scope

In v1, every token has the **same permissions as the user who owns it**. Read-only access to every paired appliance on the account. Per-token / per-site scoping is a v2 feature.

## Rate limits

10,000 requests per token per calendar month. Per-token usage is reported on `/api/v1/me` under `rate_limit.per_month_used`. Limits aren't hard-enforced in v1. We report them; we'll start gating in v1.1 once we have real clients hitting the ceiling.

## What you can't do with a token

- Pair / unpair appliances (use the dashboard)
- Change rate limits or plan tier (cloud admin only)
- Access another user's data (token is scoped to its owner)
- Make state-changing calls (v1 is read-only; writes deferred to v3)
