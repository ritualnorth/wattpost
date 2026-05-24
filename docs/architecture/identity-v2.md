# Identity v2 — Design RFC

**Status:** DRAFT (Phase 0 of EPIC #301)
**Author:** Claude (co-architect with Ritual North)
**Date opened:** 2026-05-24
**Target completion:** end of week
**Implementation tracker:** Tasks #303–#312

---

## Motivation

WattPost today has three independent authentication mechanisms wired
in parallel, none of which compose cleanly:

1. **LAN password** — first-boot generated, single shared credential
   per appliance. Read-only-public bypass means anyone on the LAN
   can browse Settings without it. (Concretely surfaced in Ritual North's
   conversation on 2026-05-24: "I went to the settings page and it
   didn't ask me to login.")
2. **Cloud account password + TOTP** — separate identity, separate
   realm, no relationship to the LAN credential.
3. **HMAC broker auth** — cloud signs every proxied request with a
   per-appliance `sso_secret`. Compromise of the cloud = compromise
   of every paired appliance, with no countersignature path.

Three parallel mechanisms means:

- **UX confusion** for any user who uses both cloud and LAN paths
  (two passwords, no SSO carry-over between them).
- **Security ceiling** at the weakest mechanism. Cloud account
  compromise grants full appliance control because the cloud is the
  sole authority on broker auth. There is no per-appliance counter-
  signature to bound the blast radius.
- **No room to grow** into the multi-tenancy + RBAC + audit story
  the enterprise / building-automation target customer requires
  (see memory `project_enterprise_ambition`).

Identity v2 replaces all three with one cryptographically-coherent
identity layer modelled on Home Assistant Cloud SSO, UniFi cloud
SSO, and standard OIDC patterns.

---

## Goals

1. **One identity per user.** Cloud account is the canonical
   identity. LAN access SSO's against it via OIDC redirect.
   Local-only customers (off-grid, never paired) get a local-only
   identity equivalent in shape.
2. **One credential per device pairing.** ed25519 keypair generated
   on the appliance at pair time. Public key cloud-side, private
   key never leaves the appliance. Replaces the shared-secret
   `sso_secret` model.
3. **One token format.** Short-lived JWT with explicit scopes,
   issued by an OIDC server in the cloud. Same token verified the
   same way across dashboard, broker, kiosk, command queue.
4. **Cloud compromise has a hard ceiling.** Without the per-device
   private key, a compromised cloud cannot forge new commands —
   only relay ones the appliance has already signed. Destructive
   commands require a fresh user re-auth on top.
5. **Offline-first still works.** Appliance functions for N days
   without cloud reachability via a pre-cached refresh token; an
   explicit local-emergency credential is the floor.
6. **Standards-compliant.** OIDC + WebAuthn + mTLS + JWT — all
   primitives an auditor can recognise. No bespoke crypto, no
   home-rolled token format.
7. **SOC2-evidence-ready.** Audit log entries cryptographically
   signed by both appliance and cloud, hash-chained for tamper
   detection.

## Non-goals

- Backwards compatibility forever. We'll support v1 auth alongside
  v2 for one release cycle, then drop v1 entirely. No long-tail
  legacy code.
- Hardware-backed key storage *required* in v2 (#312 is optional
  Phase 10). Software-encrypted private key on disk is the
  v2 baseline; HSM/secure-element is an enterprise upgrade.
- Federation with external IdPs (Okta, Auth0, Azure AD) — desirable
  long-term but out of scope for v2. The OIDC layer we build is the
  *foundation* that enables this later.

---

## Personas to satisfy

| Persona | Primary access path | What they expect |
|---|---|---|
| **Off-grid LAN-only** | `http://192.168.1.50/` on LAN | One login on each device, never asked again. No cloud account required. Works fully offline. |
| **Cloud customer on LAN** | `http://192.168.1.50/` on LAN | One identity (their cloud account). No second password. Passkey or TOTP. |
| **Cloud customer remote** | `https://<slug>.wattpost.cloud` | Same identity, no extra step. Works anywhere. |
| **Family member / shared display** | `?token=<JWT>` kiosk URL | No login at all, scoped read-only access. Owner controls expiry + revocation. |
| **Installer (multi-site)** | Same as cloud customer | Fleet-wide actions require re-auth. Audit log records every action. |
| **Building-automation operator** (future) | Cloud + role-based delegation | Site engineer can see + manage their building's sites only. CIO sees all. Audit log shows who did what. |

---

## Architecture overview

### Trust roots

| Key | Generated where | Lives where | Used for |
|---|---|---|---|
| **Cloud signing key** (ed25519) | Cloud, at first cloud deploy | Cloud KMS / sealed file (rotated quarterly) | Signing JWTs the appliance verifies |
| **Cloud TLS cert** | Let's Encrypt | Cloud (CF Tunnel terminates) | Standard HTTPS |
| **Cloud client-cert CA** (Phase 6) | Cloud, at deploy | Cloud (offline root + active intermediate) | Issuing appliance client certs for mTLS heartbeat |
| **Appliance keypair** (ed25519) | Appliance, at pair time | Appliance disk (libsodium sealed-box, key from machine-id+entropy) | Signing commands the cloud verifies, signing audit log entries |
| **Appliance client cert** (Phase 6) | Issued by cloud CA at pair time | Appliance disk (alongside keypair) | mTLS heartbeat auth |
| **User WebAuthn credentials** | User device | User device (TPM/secure-enclave) | Primary user authentication |
| **User TOTP secret** | Cloud, on user opt-in | Cloud (encrypted at rest) | 2FA fallback when WebAuthn unavailable |
| **Backup encryption key** (#300) | Appliance, at pair time | Appliance disk + offered to user as recovery phrase | Encrypting cloud-stored backups |
| **JWT signing key (kiosk-local)** | Appliance, at first kiosk-token mint | Appliance disk | Signing JWTs for LAN-only kiosk URLs |

### JWT shape

```json
{
  "iss": "https://wattpost.cloud",         // cloud-issued
                                            // or "https://<slug>.wattpost.cloud" for appliance-issued kiosk tokens
  "sub": "user_42",                         // cloud account id, or "kiosk_<uuid>" for kiosk tokens
  "aud": "appliance_137",                   // specific appliance id this token works against
  "iat": 1716553200,
  "exp": 1716554100,                        // 15 min for user tokens, configurable for kiosk
  "jti": "01J5...",                         // ulid for revocation list
  "scope": "dashboard:read dashboard:write appliance:admin",
  "amr": ["webauthn"],                      // authentication methods used; "totp", "password", "kiosk-token"
  "acr": "high",                            // assurance level: "high" (fresh webauthn), "med" (totp), "low" (long-cookie), "kiosk"
  "wp_appliance_kid": "ed25519:<fingerprint>"  // public-key fingerprint of the appliance this token was issued for
}
```

Signed by the cloud's ed25519 key, verified by appliance against
cached cloud public key.

### Scopes

```
dashboard:read              — view tiles, devices, history, energy
dashboard:write             — change rules, add/edit devices, settings
kiosk:read                  — narrow read-only subset for shared displays
appliance:admin             — pairing, unpair, update, restore, disk_cleanup
appliance:command:destructive  — destructive subset (restore, fleet update, delete site, billing)
                                 requires acr=high
billing:read
billing:write
account:read
account:write               — change password, manage 2FA, manage WebAuthn keys
fleet:read                  — multi-site view for installer / building-operator
fleet:write                 — bulk operations across sites
admin:tenant                — tenant admin (RBAC, user invites)
admin:platform              — WattPost staff only
```

Destructive operations check both scope AND `acr` claim — a long-
lived "remember me" session has scope=dashboard:write but acr=low,
so it can't trigger restore without a re-auth bump to acr=high.

### Sequence: first pair (cloud customer)

```
User                Browser              Cloud                       Appliance
 │                    │                    │                           │
 │  "Pair this        │                    │                           │
 │   appliance"       │                    │                           │
 ├──tap────────────► │                    │                           │
 │                    │  GET /pair/code    │                           │
 │                    ├──auth=user───────► │                           │
 │                    │                    │  generate 6-digit code,   │
 │                    │                    │  store {code → user_id,   │
 │                    │                    │         expires=2min}     │
 │                    │  ◄──{code}─────────┤                           │
 │  "Enter code on    │                    │                           │
 │   the appliance"   │                    │                           │
 │                    │                    │                           │
 │  ◄────────────────►│ Setup wizard:                                  │
 │                    │ "Enter pairing code"                           │
 │                    │ ──POST /api/cloud/pair─{code}──────────────────►│
 │                    │                    │                           │
 │                    │                    │ ◄─POST /pair/exchange────┤
 │                    │                    │     {code}                │
 │                    │                    │  validate code            │
 │                    │                    │  → user_id                │
 │                    │                    │                           │
 │                    │                    │                  generate ed25519
 │                    │                    │                  keypair on appliance
 │                    │                    │                           │
 │                    │                    │  ─POST /pair/finalize────►│ (request public key)
 │                    │                    │                  ◄──{pub, fingerprint}
 │                    │                    │ store {appliance_id,      │
 │                    │                    │        pub_key,           │
 │                    │                    │        client_cert (#308)}│
 │                    │                    │ ─{appliance_id,           │
 │                    │                    │   cloud_jwks_url,         │
 │                    │                    │   refresh_token}─────────►│
 │                    │                    │                  cache cloud public key
 │                    │                    │                  store refresh_token
 │                    │ ◄──{paired: true}──┤                           │
 │  ◄──"Paired!"──────┤                    │                           │
```

Key properties:
- **No long-lived shared secret is ever transmitted.** The appliance
  generates its private key locally; only the public key crosses the
  wire.
- **Pairing code is short-lived (2 min) and single-use.** Bound to
  the user that minted it.
- **Cloud's public key (JWKS) is fetched at pair time and cached
  forever** (with rotation handled via JWKS endpoint kid header).
- **Refresh token is the only credential the appliance holds for
  the cloud account** — short-lived; rotated on each use. Cloud-side
  revocable.

### Sequence: LAN login (cloud customer)

```
User              Browser                 Appliance              Cloud
 │                  │                        │                     │
 │ open             │                        │                     │
 │ http://10.0.0.5/ │                        │                     │
 ├────────────────► │ ─GET /───────────────► │                     │
 │                  │                        │ no session cookie?  │
 │                  │ ◄─302 Location: cloud  │                     │
 │                  │   /oidc/authorize      │                     │
 │                  │   ?return=http://10.0  │                     │
 │                  │   .0.5/auth/callback   │                     │
 │                  │   &client=apl_137      │                     │
 │                  │   &scope=dash:rw       │                     │
 │                  │                        │                     │
 │                  │ ─GET /oidc/authorize──────────────────────►  │
 │                  │                        │                     │ check user session
 │                  │                        │                     │ → not logged in
 │                  │ ◄─302 Location:                              │
 │                  │   /login?continue=...                        │
 │                  │                                              │
 │  type            │                                              │
 │  passkey         │ ─POST /login/webauthn ─────────────────────► │
 ├────────────────►│                                               │ verify assertion
 │                  │ ◄─302 Location:                              │ → user_42, amr=webauthn
 │                  │   /oidc/authorize?...                        │
 │                  │                                              │
 │                  │ ─GET /oidc/authorize──────────────────────►  │
 │                  │                                              │ mint code,
 │                  │                                              │ bind to {user_42,
 │                  │                                              │   apl_137, scope}
 │                  │ ◄─302 Location:                              │
 │                  │   http://10.0.0.5/auth/callback?code=xxx     │
 │                  │                                              │
 │                  │ ─GET /auth/callback──► │                     │
 │                  │   ?code=xxx            │                     │
 │                  │                        │ ─POST /oidc/token─► │
 │                  │                        │   {code, client_id} │
 │                  │                        │                     │ exchange code → JWT
 │                  │                        │ ◄──{access, refresh}│
 │                  │                        │ verify JWT sig      │
 │                  │                        │ against cached      │
 │                  │                        │ cloud JWKS          │
 │                  │                        │ issue local session │
 │                  │                        │ cookie scoped to    │
 │                  │                        │ this browser        │
 │                  │ ◄─302 Location: /─Set-Cookie:                │
 │                  │   wp_session=...       │                     │
 │                  │                        │                     │
 │                  │ ─GET /────────────────►│                     │
 │                  │                        │ session valid       │
 │                  │ ◄─dashboard────────────┤                     │
```

Key properties:
- **User logs in once on the cloud** (passkey or TOTP). No separate
  LAN password.
- **Appliance does NOT call the cloud during the cookie's lifetime**
  — JWT signature verification is offline using the cached JWKS.
- **Refresh happens silently** when JWT expires (browser still has
  cloud session cookie via cloud's domain).
- **If cloud is unreachable**, the LAN session cookie is still
  valid for its full lifetime. Login fails new sessions but
  existing ones keep working until cookie expiry. After that,
  fall through to offline mode (Phase 4).

### Sequence: cloud-issued destructive command

```
User           Cloud UI            Cloud                    Appliance
 │                │                  │                       │
 │ click          │                  │                       │
 │ "Restore       │                  │                       │
 │  backup"       │                  │                       │
 ├──────────────► │                  │                       │
 │                │ ─POST /sites/137/commands ─{kind=restore}► │
 │                │                  │ check scope:          │
 │                │                  │  → has dash:write,    │
 │                │                  │    needs apl:cmd:destr│
 │                │                  │ check acr:            │
 │                │                  │  → "low" (long cookie)│
 │                │                  │ → reject with         │
 │                │                  │   reauth_required     │
 │                │ ◄─401 reauth_required {amr=[webauthn]}  │
 │                │                  │                       │
 │  ◄ passkey     │                                          │
 │    prompt      │                                          │
 │ ├──tap────────►│ ─POST /reauth/webauthn ───────────────► │ mint fresh
 │                │                  │                       │ session with
 │                │                  │                       │ acr=high
 │                │                  │                       │ valid 5min
 │                │ ◄─OK ────────────┤                       │
 │                │                  │                       │
 │                │ ─POST /sites/137/commands ─{restore}───► │
 │                │                  │ scope OK, acr=high OK │
 │                │                  │                       │
 │                │                  │ mint command JWT      │
 │                │                  │ signed by cloud,      │
 │                │                  │ scope=apl:cmd:destr,  │
 │                │                  │ aud=apl_137,          │
 │                │                  │ wp_action_hash=H(payload)│
 │                │                  │                       │
 │                │                  │ insert into queue,    │
 │                │                  │ next heartbeat picks  │
 │                │                  │ up                    │
 │                │                  │                       │
 │                │                  │ ─heartbeat response──►│
 │                │                  │   {cmd_jwt}           │
 │                │                  │                       │ verify JWT sig
 │                │                  │                       │ against cached
 │                │                  │                       │ cloud JWKS
 │                │                  │                       │ verify acr=high
 │                │                  │                       │ verify hash matches
 │                │                  │                       │ payload
 │                │                  │                       │ execute restore
 │                │                  │                       │ log signed audit
 │                │                  │                       │ entry
```

Key properties:
- **Destructive commands carry the cloud JWT *and* a hash of the
  payload.** Cloud can't queue a "restore" that points at a
  different backup file after the fact.
- **Appliance verifies the JWT before acting.** Compromised cloud
  needs the cloud's private key to mint a destructive-scope JWT;
  if the cloud is breached, key rotation invalidates outstanding
  tokens immediately.
- **Re-auth window is 5 min.** Subsequent destructive actions in
  the same flow don't re-prompt; first one after 5 min does.

### Offline mode

When the appliance can't reach the cloud:

1. **Existing local sessions keep working** until their cookie
   expires (default 90 days, configurable).
2. **New LAN logins** attempt the OIDC redirect, fail (no cloud
   reachable), and fall through to the **emergency local login**.
3. **Emergency local credentials** are set during pairing OR by the
   user explicitly in Settings. Username + password, rate-limited,
   audit-logged. Intended as the floor — "the internet is down and
   I need to look at my dashboard".
4. **Destructive commands cannot be triggered offline.** They
   require a fresh cloud auth (acr=high), which is impossible
   without the cloud. This is intentional — the only time you
   should be running a restore is when you have full faith in the
   auth chain.
5. **Local-only appliances (never paired)** use emergency local
   credentials as their primary auth. No OIDC dance — login form
   posts straight to local password verification. This is the
   off-grid customer who never wanted the cloud.

### Kiosk shares (unified)

All kiosk URLs are `?token=<JWT>`. The JWT has:

```json
{
  "iss": "https://wattpost.cloud" | "https://<slug>.wattpost.cloud",
  "sub": "kiosk_01J5...",
  "aud": "appliance_137",
  "scope": "kiosk:read",
  "exp": 1717158000,
  "acr": "kiosk",
  "wp_kiosk_label": "Living room display",
  "wp_kiosk_pin_ip": "10.0.0.42"           // optional first-IP pin
}
```

Cloud-issued kiosk JWTs (shared via `<slug>.wattpost.cloud/k/<token>`)
have `iss=cloud` and validate against the cloud's public key.
Appliance-issued kiosk JWTs (for local-only customers without a
cloud account) have `iss=<slug>.wattpost.cloud` and validate
against the appliance's own kiosk-signing key.

**Same JWT validation code path** — issuer determines which key to
verify against, scope determines what's allowed. No parallel
mechanisms.

---

## Technical decisions (resolved 2026-05-24)

These were originally framed as "open questions"; each has a
standard industry answer. Resolved here without surfacing to Ritual North
per [[user_role]] — these are technical implementation calls, not
strategic ones.

| # | Decision | Reasoning |
|---|---|---|
| **1. Refresh-token rotation** | **Rotate on every use.** Old token marked `rotated_to=<new>` and rejected if presented again. | OIDC RFC 6749 §6 + OAuth 2.1 draft. Detects token theft (if both old and new ever present, both revoked + user alerted). Negligible load at our cadences. |
| **2. Cloud signing key rotation** | **Quarterly. 48h overlap window** during which both old and new `kid` validate. Old key archived after overlap; can still verify already-issued tokens until their natural expiry (15 min). | Industry standard for OIDC IdPs (Okta, Auth0, AWS Cognito all use ~90-day rotation). 48h overlap handles clock skew + caching staleness in appliance JWKS caches. |
| **3. Cloud key storage** | **Pre-launch:** ed25519 private keys sealed (libsodium sealed-box) on disk, master key in deploy-time env var on the Contabo VPS. **Post-paying-customers:** migrate to **AWS KMS** (or **Hashicorp Vault** if we keep self-hosted). Migration is a one-shot key-import job; can defer until billing is live. | KMS-managed keys are the SOC2 baseline. Pre-launch sealed-box-on-disk is fine for "few testers, no real customer data yet"; document the migration path so we don't have to think about it again. Don't ship Vault before we have someone to ask "should we just use AWS KMS" — pre-mature infra cost. |
| **4. WebAuthn Relying Party ID** | **`wattpost.cloud`** (apex domain). Passkeys then work across `wattpost.cloud`, `app.wattpost.cloud`, `<slug>.wattpost.cloud`. | WebAuthn RP IDs must be the registrable suffix or a subdomain of it; using the apex means all our subdomains share credentials. Industry pattern (GitHub uses `github.com`, Google uses `google.com`). |
| **5. mTLS cert renewal** | **1y validity, auto-renew 30 days before expiry** via fresh CSR over the existing heartbeat channel. If renewal fails for 30 days straight, cloud surfaces a "Cert expiring — manual action required" alert on the cloud dashboard. | Lets-Encrypt-style cadence. 30-day window gives 6 weekly retry chances before user intervention. Standard pattern in cloud-edge architectures. |
| **6. JWT library** | **`authlib`** for signing + verification on cloud. **`pyjwt`** for verify-only on appliance (smaller dep, simpler API). | `python-jose` unmaintained since 2024 (per pypi). `authlib` is the actively-maintained spec-complete OIDC + OAuth2 lib for Python. `pyjwt` is a strict subset — fine for appliance which only verifies tokens, never signs cloud-bound ones. |
| **7. Schema migration mechanism** | **Alembic, additive-only during v1+v2 cycle.** New tables added in v0.2.0; legacy `appliances.sso_secret` + `bearer_token` columns dropped in v0.3.0. Each migration tested against a copy of prod data before deploy. | Standard SQLAlchemy + Alembic stack we already use. Additive migrations are safe to roll back; column drops only happen after deprecation cycle ends. |
| **8. Breaking-change handling** | **One-tap re-pair migration** via the cloud banner described in the migration section. Every paired appliance shows "Security upgrade available" until upgraded. v0.3.0 drops v1 entirely; appliances on v1 at that point are forced to re-pair from scratch (well-flagged in changelog + email). | Matches the "upgrade your password" UX every serious SaaS uses (Stripe, GitHub). One-tap because the appliance does all the keypair work; user only consents. |

Decision-maker note: every one of these was a textbook industry-
best-practice answer. No strategic content for Ritual North to weigh in
on. RFC is implementation-ready; Phase 1 (#303) is unblocked.

---

## Phasing (mapped to backlog tasks)

See EPIC #301 + sub-tasks #303–#312.

Phase 0 (this doc) blocks everything. Phase 1 (#303) is the
foundation everything else stands on. Phases 2 (#304) and 3 (#305)
together implement the SSO flow. Phase 4 (#306) handles offline.
Phase 5 (#307) and 6 (#308) are parallelisable. Phase 7 (#309)
unifies kiosks. Phase 8 (#310) wires audit signing. Phase 9 (#311)
adds re-auth gating. Phase 10 (#312) is optional hardware key
storage.

---

## Database schema

### Cloud — new tables

```sql
-- OIDC clients. The appliance (per pairing) is registered as a
-- client. Future: federated IdPs (Okta etc) would also live here.
CREATE TABLE oidc_clients (
    client_id        VARCHAR PRIMARY KEY,
    appliance_id     INTEGER REFERENCES appliances(id) ON DELETE CASCADE,
    redirect_uris    TEXT[] NOT NULL,              -- e.g. {'http://10.0.0.5/auth/callback'}
    allowed_scopes   TEXT[] NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at       TIMESTAMPTZ
);

-- Short-lived (60s) authorization codes from /oidc/authorize.
-- Single-use, bound to {user, client, scope, redirect, PKCE chal}.
CREATE TABLE oidc_authorization_codes (
    code             VARCHAR PRIMARY KEY,           -- ulid
    user_id          INTEGER NOT NULL REFERENCES users(id),
    client_id        VARCHAR NOT NULL REFERENCES oidc_clients(client_id),
    scope            TEXT NOT NULL,
    redirect_uri     VARCHAR NOT NULL,
    code_challenge   VARCHAR NOT NULL,              -- PKCE S256
    amr              TEXT[],                        -- auth methods used
    acr              VARCHAR NOT NULL,              -- assurance level
    expires_at       TIMESTAMPTZ NOT NULL,
    used_at          TIMESTAMPTZ
);

-- Long-lived refresh tokens. Rotated on use. Cloud-side revocable.
CREATE TABLE oidc_refresh_tokens (
    token_hash       VARCHAR PRIMARY KEY,           -- sha256(token), never store plaintext
    user_id          INTEGER NOT NULL REFERENCES users(id),
    client_id        VARCHAR NOT NULL REFERENCES oidc_clients(client_id),
    scope            TEXT NOT NULL,
    issued_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at       TIMESTAMPTZ NOT NULL,           -- 90d default
    last_used_at     TIMESTAMPTZ,
    rotated_to       VARCHAR,                       -- if rotated, points at new token_hash
    revoked_at       TIMESTAMPTZ
);

-- Revocation list for issued JWTs (jti). Cleared by a periodic
-- janitor 24h after the JWT's natural expiry.
CREATE TABLE oidc_token_revocation (
    jti              VARCHAR PRIMARY KEY,
    expires_at       TIMESTAMPTZ NOT NULL,
    revoked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason           VARCHAR                        -- 'user_logout', 'admin_revoke', ...
);

-- Cloud signing keys for JWTs. Rotation: a new key appears here,
-- becomes "active" after a 24h overlap, old key kept for 48h more
-- to verify any in-flight tokens, then archived.
CREATE TABLE oidc_signing_keys (
    kid              VARCHAR PRIMARY KEY,
    alg              VARCHAR NOT NULL,              -- 'EdDSA' (ed25519)
    public_jwk       JSONB NOT NULL,
    private_pem      BYTEA NOT NULL,                -- KMS-sealed in prod
    status           VARCHAR NOT NULL,              -- 'active', 'rotating', 'archived'
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at     TIMESTAMPTZ,
    archived_at      TIMESTAMPTZ
);

-- WebAuthn credentials per user. Multiple per user (phone passkey,
-- laptop passkey, hardware key as backup).
CREATE TABLE user_webauthn_credentials (
    id               BIGSERIAL PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credential_id    BYTEA NOT NULL UNIQUE,         -- WebAuthn credential ID
    public_key       BYTEA NOT NULL,                -- COSE-encoded public key
    sign_count       INTEGER NOT NULL DEFAULT 0,    -- replay protection
    transports       TEXT[],                        -- 'internal', 'usb', 'nfc', 'ble'
    label            VARCHAR,                       -- user-given name e.g. "iPhone 15"
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at     TIMESTAMPTZ,
    revoked_at       TIMESTAMPTZ
);

-- Per-appliance ed25519 public keys (private stays on appliance).
-- Generated at pair time. Cloud uses this to verify counter-signed
-- responses + signed audit log entries.
CREATE TABLE appliance_keypairs (
    appliance_id     INTEGER PRIMARY KEY REFERENCES appliances(id) ON DELETE CASCADE,
    public_key       BYTEA NOT NULL,                -- raw 32-byte ed25519 pub key
    fingerprint      VARCHAR NOT NULL UNIQUE,       -- hex sha256(public_key)[:16]
    paired_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_verified_at TIMESTAMPTZ,                   -- bumped on successful counter-sign
    rotated_from     INTEGER REFERENCES appliance_keypairs(appliance_id)
);

-- Hash-chained audit log (#310). Each entry signed by either cloud
-- (issuer='cloud') or appliance (issuer='appliance'); prev_hash
-- links into the chain. Tamper detection = re-walk chain + verify
-- signatures.
CREATE TABLE signed_audit_log (
    id               BIGSERIAL PRIMARY KEY,
    appliance_id     INTEGER REFERENCES appliances(id) ON DELETE CASCADE,
    user_id          INTEGER REFERENCES users(id),
    issuer           VARCHAR NOT NULL,              -- 'cloud' or 'appliance'
    event_type       VARCHAR NOT NULL,              -- 'login', 'cmd:restore', 'pair', ...
    event_payload    JSONB NOT NULL,
    prev_hash        VARCHAR,                       -- sha256 of previous row's signed_repr
    signed_repr      VARCHAR NOT NULL,              -- canonical-JSON-then-sign payload
    signature        BYTEA NOT NULL,                -- ed25519 sig over signed_repr
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    INDEX (appliance_id, occurred_at DESC)
);

-- Client cert issuance for mTLS heartbeat (Phase 6). One active
-- cert per appliance; renewed 30d before expiry.
CREATE TABLE appliance_client_certs (
    id               BIGSERIAL PRIMARY KEY,
    appliance_id     INTEGER NOT NULL REFERENCES appliances(id) ON DELETE CASCADE,
    cert_pem         TEXT NOT NULL,
    serial           VARCHAR NOT NULL UNIQUE,
    not_before       TIMESTAMPTZ NOT NULL,
    not_after        TIMESTAMPTZ NOT NULL,
    revoked_at       TIMESTAMPTZ,
    issued_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Cloud — modified tables

```sql
ALTER TABLE appliances
  ADD COLUMN identity_v2_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN v2_upgraded_at TIMESTAMPTZ;
  -- sso_secret + bearer_token kept for v1 fallback during migration cycle.
  -- Dropped in v0.3.0 after every appliance has upgraded.

ALTER TABLE users
  ADD COLUMN preferred_amr VARCHAR DEFAULT 'webauthn';  -- 'webauthn' | 'totp' | 'password'
  -- password column kept; deprecated to fallback-only for v2 customers.
```

### Appliance — on-disk layout

```
/var/lib/wattpost/keys/
├── appliance.ed25519.sealed     # libsodium sealed-box, key derived from
│                                # machine-id + first-boot entropy.
│                                # Phase 10 swaps this for ATECC608A.
├── cloud_jwks.json              # cached cloud public keys (refreshed
│                                # every 24h via /oidc/jwks)
├── refresh_token.sealed         # current refresh token (rotated on use)
├── emergency_password.argon2id  # local-only fallback credential
├── client_cert.pem              # mTLS client cert (Phase 6)
└── client_key.sealed            # mTLS client key, same sealed-box pattern
```

### Appliance — SQLite tables

```sql
-- Browser cookies for local sessions. 90d default expiry, scoped
-- to the User-Agent + IP that established the session.
CREATE TABLE local_sessions (
    id              TEXT PRIMARY KEY,              -- random 32 bytes hex
    user_sub        TEXT NOT NULL,                 -- 'user_42' or 'local'
    user_agent      TEXT NOT NULL,
    issued_ip       TEXT NOT NULL,
    issued_at       TIMESTAMP NOT NULL,
    expires_at      TIMESTAMP NOT NULL,
    scope           TEXT NOT NULL,
    acr             TEXT NOT NULL,
    acr_high_until  TIMESTAMP,                     -- 5min re-auth window
    last_seen_at    TIMESTAMP
);

-- Appliance-side audit log. Same hash-chain shape as cloud's.
-- Sync'd to cloud opportunistically via heartbeat for redundancy.
CREATE TABLE audit_log_local (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,
    event_payload   TEXT NOT NULL,                 -- JSON
    prev_hash       TEXT,
    signed_repr     TEXT NOT NULL,
    signature       BLOB NOT NULL,                 -- ed25519 sig
    occurred_at     TIMESTAMP NOT NULL,
    synced_to_cloud BOOLEAN NOT NULL DEFAULT 0
);

-- Locally-issued kiosk tokens (for local-only customers, see
-- Phase 7). Stored so we can list + revoke; the JWT itself is
-- self-contained and verified statelessly.
CREATE TABLE kiosk_tokens_local (
    jti             TEXT PRIMARY KEY,
    label           TEXT,
    issued_at       TIMESTAMP NOT NULL,
    expires_at      TIMESTAMP,                     -- null = no expiry
    pin_ip          TEXT,                          -- first-IP pin if set
    revoked_at      TIMESTAMP
);
```

---

## Cloud OIDC endpoint detail

### Discovery

```
GET /.well-known/openid-configuration
```

Returns standard OIDC discovery doc. Custom fields:
- `scopes_supported` — the WattPost scope catalogue
- `acr_values_supported` — `["high", "med", "low", "kiosk"]`
- `wp_supported_amr` — `["webauthn", "totp", "password"]`

### JWKS

```
GET /oidc/jwks
```

Returns active + rotating signing keys. Cache headers: `max-age=86400`
on the response; appliances refresh daily. Rotation handled by
including both old + new kid during the rotation window.

### Authorize

```
GET /oidc/authorize?
  client_id=apl_137_lan
  &response_type=code
  &scope=dashboard:write+appliance:admin
  &redirect_uri=http%3A%2F%2F10.0.0.5%2Fauth%2Fcallback
  &code_challenge=<S256(verifier)>
  &code_challenge_method=S256
  &state=<csrf>
  &nonce=<replay>
```

PKCE mandatory (no public-client-without-PKCE allowed). State + nonce
mandatory.

User-not-logged-in path: 302 to `/login?continue=<urlencoded
original /oidc/authorize url>`. After login, replay the authorize.

User-logged-in path: validate client_id + scope, mint code, 302 to
`<redirect_uri>?code=<code>&state=<state>`.

### Token

```
POST /oidc/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=<code>
&redirect_uri=<must match authorize>
&client_id=<client_id>
&code_verifier=<original PKCE verifier>
```

Returns:
```json
{
  "access_token": "<JWT>",
  "token_type": "Bearer",
  "expires_in": 900,
  "refresh_token": "<opaque>",
  "scope": "dashboard:write appliance:admin",
  "id_token": "<JWT — same claims minus scope>"
}
```

Refresh flow:
```
grant_type=refresh_token
&refresh_token=<token>
&client_id=<client_id>
```

Refresh token rotation: response includes a new refresh token; old
one marked `rotated_to=<new>` and unusable.

### Userinfo

```
GET /oidc/userinfo
Authorization: Bearer <JWT>
```

Returns minimal user profile (sub, email, name, tenant_id, roles).

### Revoke

```
POST /oidc/revoke
Authorization: Bearer <JWT>
Content-Type: application/x-www-form-urlencoded

token=<token>
&token_type_hint=refresh_token|access_token
```

Token type access_token: add jti to revocation list. Token type
refresh_token: mark refresh row revoked.

### Re-auth bump

```
POST /reauth/webauthn
Authorization: Bearer <current_jwt>

{ "credential_response": {...WebAuthn AuthenticatorAssertionResponse...} }
```

Returns a new JWT with acr=high, exp=now+5min. Old JWT not revoked
but acr=high commands prefer the new one.

```
POST /reauth/totp
Authorization: Bearer <current_jwt>

{ "code": "123456" }
```

Same shape, TOTP path.

### Pairing extensions

```
POST /pair/code
Authorization: Bearer <cloud_session_jwt>

{ }
```

Returns:
```json
{
  "code": "ABC-XYZ",        // 6-7 char human-typeable
  "expires_at": "2026-05-24T12:35:00Z"
}
```

Code → {user_id, scope_grant} bound in `oidc_authorization_codes`
table with a special grant_type marker.

```
POST /pair/exchange
(no auth — code is the auth)

{ "code": "ABC-XYZ" }
```

Returns:
```json
{
  "appliance_id":         137,
  "appliance_label":      "Garage Stack",
  "cloud_jwks_url":       "https://wattpost.cloud/oidc/jwks",
  "oidc_client_id":       "apl_137_lan",
  "oidc_redirect_uri":    "http://__appliance__/auth/callback",
  "client_cert_csr_url":  "https://wattpost.cloud/pair/csr",
  "refresh_token":        "<opaque>",
  "tunnel_token":         "<CF tunnel cred>"
}
```

```
POST /pair/finalize
Authorization: Bearer <refresh_token from exchange>

{
  "public_key": "<base64 ed25519 pub>",
  "fingerprint": "<hex>"
}
```

Cloud stores public key in `appliance_keypairs`, returns OK. From
this point forward the appliance is fully paired.

---

## Migration plan from v1

The fleet of paired appliances today uses HMAC `sso_secret` +
bearer token. v2 is co-deployed with v1 for one release cycle so
no appliance is forced into a hard cut-over.

### Phase 1 ships in v0.2.0

- v2 cloud endpoints all live
- v2 appliance code path exists, gated by `identity_v2_enabled` on
  the appliances row (default FALSE)
- Existing v1 code paths untouched, all functionality continues
- Cloud UI surfaces a banner: **"Security upgrade available for N
  of your appliances"** → click → walks user through the upgrade

### Per-appliance upgrade flow

1. User clicks "Upgrade" on the cloud banner for appliance X.
2. Cloud calls a v1 command queue: `kind=upgrade_to_v2`.
3. Appliance picks up the command on next heartbeat (bearer-auth'd).
4. Appliance generates ed25519 keypair on-device.
5. Appliance POSTs `/api/internal/pair/upgrade-to-v2` to cloud with:
   - Old bearer token (v1 auth proves it's the same device)
   - New public key
   - Fingerprint
6. Cloud verifies bearer token matches `appliances.bearer_token`,
   stores public key in `appliance_keypairs`, flips
   `appliances.identity_v2_enabled = TRUE`, returns:
   - oidc_client_id
   - refresh_token (first v2 token, replaces bearer)
   - JWKS url
   - Client cert + key (if Phase 6 shipped)
7. Appliance writes its new keys to disk, deletes the old
   `bearer_token` config field.
8. Heartbeat from this appliance now uses v2 mechanism.
9. Cloud-side: `appliances.bearer_token` is kept but marked
   deprecated; appliance ignores it.

### Edge cases

- **Appliance offline at upgrade time.** Command queues; runs on
  next heartbeat. Banner stays on cloud UI until upgrade completes.
- **Upgrade fails mid-flight** (network blip during finalize).
  Appliance keypair on disk + v1 bearer still works. Cloud sees no
  v2 pairing; banner remains; user can retry. Idempotent.
- **Fleet upgrade.** Installer-tier user with N appliances clicks
  "Upgrade all" on the dashboard banner; commands queue for each;
  user sees per-appliance status. Same audit/safety chain as
  fleet update.
- **User can't upgrade** (e.g. local-only customer with no cloud
  account). They're already on v1 only; the upgrade banner doesn't
  show; they continue on v1 until v0.3.0 drops it. By v0.3.0 we'll
  have shipped a local-only-no-cloud upgrade path (set emergency
  password on the appliance, generate appliance-issued kiosk
  tokens, no cloud at all).

### Phase 2 ships in v0.3.0

- Drop v1 code paths entirely
- `appliances.sso_secret` + `bearer_token` columns dropped
- Any appliance that hasn't upgraded by this point is forced into
  a re-pair flow

---

## Threat model

### Attackers

| ID | Attacker | Capability | What they want |
|---|---|---|---|
| **T1** | Internet randomer | Scans `*.wattpost.cloud`, public broker URLs | Any unauthenticated access |
| **T2** | Stolen cloud session | Phished or shoulder-surfed user, valid session cookie | Pivot to LAN, exfil data, persistent backdoor |
| **T3** | Compromised cloud server | Root on a wattpost.cloud VM (worst-case) | Mass exfil across all customers, install backdoors |
| **T4** | LAN attacker | On the customer's wifi (guest, neighbour, IoT pivot) | Device access, lateral movement |
| **T5** | Physical attacker | Pulls SD card or USB-sniffs the appliance | Extract secrets, persistent install |
| **T6** | Insider | WattPost staff with cloud admin access | Targeted data exfil, evidence tampering |
| **T7** | Supply chain | Compromised dep, GHCR account, npm package | Inject code into shipped releases |

### What v2 protects against

| Threat | Mitigation in v2 |
|---|---|
| T1 (internet) | TLS everywhere; 2FA / passkey on all login; rate limits (#156 extended in Phase 9); DDoS hardening (#145); no anonymous /healthz/deep leaks (#155) |
| T2 (stolen session) | acr=low for long cookies — can read dashboard but cannot trigger destructive ops without fresh re-auth (Phase 9). Restore is impossible without webauthn tap. Client-encrypted backups (#300) mean even if attacker downloads them they can't decrypt. |
| T3 (compromised cloud) | **The core architectural win.** Compromised cloud holds: cloud signing key (can mint JWTs), refresh tokens (can extend sessions). Does NOT hold: appliance private keys (can't forge appliance-signed audit entries; can't make appliance trust forged data on restore). Hash-chained audit log signed by appliances means tampering detectable. Backup encryption with appliance-held key means cloud can't decrypt or forge backups. |
| T4 (LAN attacker) | Strict-default auth (no anonymous GET). LAN access requires either OIDC SSO via cloud (so attacker needs the user's cloud creds) or the emergency local credential (rate limited, audit logged). No more browse-Settings-as-anon. |
| T5 (physical) | Software keys are sealed against a machine-id-derived secret — meaningful work to extract but not absolute. Phase 10 with ATECC608A or YubiKey provides hardware-bound key storage. Audit log + remote attestation (future) can detect tamper. |
| T6 (insider) | Audit log is appliance-signed — staff at cloud can't backdoor entries without appliance cooperation. Tenant isolation in DB (RBAC). Per-action approval requirements for staff cross-tenant queries. SOC2 evidence trail backs this. |
| T7 (supply chain) | OUT OF SCOPE for v2 — separate hardening story. Mitigations to ship later: cosign-signed Docker images, SBOM in every release, dependency pinning + scanning, reproducible builds. Backlog as separate epic. |

### Residual risks

These are bounded by v2 but not eliminated:

- **Cloud signing key extracted via T3 + run a long-running side channel.** Attacker can mint valid JWTs for the duration. Mitigation: short-lived JWTs (15min), rotation tooling for emergency key roll, monitoring on `/oidc/token` request anomalies. Still better than today (where bearer token is forever).
- **User loses both their passkey AND their TOTP backup AND their backup recovery code.** Bricks the account. Mitigation: at signup, require ≥2 auth methods (passkey + TOTP at minimum); show backup recovery codes during signup with "print this" CTA. Standard for any account-secure product.
- **Customer's own LAN has malware that intercepts the OIDC redirect.** SSL is the bar; we can't fully defend a compromised LAN. Mitigation: HSTS on all wattpost.cloud subdomains, encourage WAN access via broker URL when on hostile networks.

### Abuse cases worth modeling explicitly

- "Disgruntled installer with fleet:write tries to nuke a customer's site after losing the account" — destructive command requires fresh re-auth + audit log shows the trigger; cloud-side allowlist of which installers can act on which sites enforces tenancy.
- "Customer wants to revoke a stolen phone with passkeys on it" — account settings show passkey list with last-used dates + per-key revoke. Revoking a passkey invalidates all sessions ever issued with that passkey present in amr.
- "Cloud staff member tries to query a customer's database for 'support reasons'" — every cross-tenant query through the admin tool is logged in `signed_audit_log` (signed cloud-side; can be cross-checked against the customer's appliance log). Customer can export their audit history.

---

## Performance characteristics

| Operation | Cost | User-perceived latency |
|---|---|---|
| JWT verify (ed25519) | ~100µs | imperceptible |
| OIDC redirect first LAN login | 2 cloud round-trips (~800ms) | ~1s once per device per 90 days |
| LAN visit with valid cookie | 0 cloud round-trips | imperceptible |
| Refresh token rotation | 1 cloud round-trip | imperceptible (background) |
| WebAuthn assertion | 1 device-local (TPM/secure-enclave) op | ~500ms (passkey tap) |
| mTLS handshake (Phase 6) | 1 TLS round-trip | ~50ms first heartbeat after restart; keepalive reuses connection |
| Audit log entry sign + write | ed25519 sign ~100µs + 1 SQLite write ~1ms | imperceptible |
| Cloud-side command verify | JWT verify + DB lookup ~5ms | imperceptible |

### Storage growth

| Data | Size per appliance per year | All cloud, 1000 appliances |
|---|---|---|
| Audit log | ~30MB (worst case, busy install) | ~30GB / yr |
| OIDC refresh tokens (active) | ~1KB at any time | ~1MB |
| OIDC auth codes (60s TTL) | ~0 (cleared) | ~0 |
| Appliance keypair row | ~200 bytes once | ~200KB |
| WebAuthn credentials per user | ~500 bytes × N keys | ~500KB / 1000 users |
| Token revocation list | ~100 bytes × revocations × 24h | <10MB |

Postgres can chew through this without breaking a sweat at 10k
appliances; nothing scales superlinearly with appliance count.

### Cloud CPU load

JWT verification on every API request: ~100µs. At 10k appliances
each heartbeating every 5 min, that's ~33 requests/sec sustained,
~3ms/sec CPU. Negligible.

WebAuthn registration peaks during user onboarding only; one-shot
ops costing a few ms each.

OIDC `authorize` + `token` paths: cheap. Maybe 1-2 per user per
day at steady state once cookies settle. ~20ms each.

### Bandwidth

OIDC redirect first-visit: ~5KB across all the round-trips.
Subsequent visits: 0 cloud bandwidth. Heartbeat unchanged (~2KB).

Audit log sync: when appliances sync local audit entries to cloud
opportunistically, ~10KB/day per appliance worst case. ~10MB/day
across 1000 appliances.

No scaling concerns at our target customer counts.

---

## Acceptance criteria for Phase 0 (this doc)

- [x] Motivation + goals + non-goals
- [x] Personas mapped to access paths
- [x] Trust roots enumerated (every key, where it lives, what
  it's used for)
- [x] JWT shape + scope catalogue
- [x] Three full sequence diagrams (pair, LAN login, destructive
  command)
- [x] Offline mode behaviour
- [x] Unified kiosk-share design
- [x] DB schema (cloud + appliance, both new and modified)
- [x] OIDC endpoint specifications
- [x] Migration plan from v1
- [x] Threat model (attackers × mitigations matrix)
- [x] Residual risks called out explicitly
- [x] Performance characteristics (latency + storage + CPU + bandwidth)
- [x] **Open questions resolved** — see "Technical decisions"
  section. All 8 closed with industry-standard answers.
- [x] **Strategic shape signed off** by Ritual North (enterprise ambition
  + best-practice posture). No further review needed — technical
  implementation calls are mine.

---

## Next steps

1. ~~Ritual North reviews this doc~~ — not needed; Ritual North is CEO, not
  engineer (see [[user_role]]). Technical decisions resolved
  above without his input.
2. ~~Resolve the 8 open questions~~ — done; see "Technical
  decisions" section.
3. Mark Phase 0 (#302) **completed** — done.
4. **Phase 1 (#303)** — appliance keypair foundation. ~1 week.
  Ready to start.
5. **Phase 5 (#307)** — WebAuthn / passkey on cloud. Parallel
  with Phase 1, doesn't block.

Phase 1 lands on its own merits (per-appliance signed commands)
even if the OIDC layer isn't finished — it's a meaningful security
upgrade on its own.
