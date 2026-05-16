# License

WattPost is **source-available, not open source**. The appliance
source ships with every SD image (`/opt/wattpost-src/`) and every
Docker image (`/app/solar_monitor/`) so you can audit and modify
exactly what runs on your hardware.

## Plain-English summary

- ✅ Audit the code, read every line, run a debugger through it.
- ✅ Modify your own copy — add a vendor driver, change a UI string,
  swap a colour, fork for a hobby project.
- ✅ Run it on your own hardware (Raspberry Pi, mini PC, RV, boat, off-grid cabin).
- ✅ Share patches back — pull requests welcome.
- ❌ Sell it (modified or not) as a product or hosted service.
- ❌ Resell it under a different name.
- ❌ Use the "WattPost" name or logo for anything other than referring
  to this project.

## The formal terms

Licensed under [**PolyForm Noncommercial 1.0.0**](https://polyformproject.org/licenses/noncommercial/1.0.0).
The full text is in [LICENSE](https://wattpost.io/) bundled with
every distribution. PolyForm is a vetted source-available licence
drafted by recognised legal experts — it's what we use because
writing a custom licence is risky and reinventing the wheel.

## The cloud tier

The code at `wattpost.cloud` (multi-site dashboard, heartbeat ingest,
Cloudflare tunnel provisioning, REST API server, billing) is **not**
distributed and **not** covered by the appliance licence. It's
private to RitualNorth Ltd.

## Commercial use

If you want to use WattPost commercially — embed it in a product,
offer it as a managed service, white-label it for installer
customers — email [support@wattpost.io](mailto:support@wattpost.io)
and we'll figure out a licence that works for both of us. We're
not anti-business; we just want to be the ones running WattPost as
a hosted product.

## Why this approach

- **Trust:** You can verify there's no spyware / phone-home / cloud
  lock-in. The source on your Pi *is* what's running.
- **Repairability:** A vendor going out of business shouldn't brick
  your batteries. With source you can keep WattPost alive even if
  we disappear.
- **Sustainability:** We want to keep building this. A pure-MIT
  licence makes that hard — a competitor can copy our work and
  undercut us on day one. PolyForm Noncommercial threads the needle:
  open for users, closed for would-be SaaS hosts.

If this annoys you, we get it. The alternative was "fully closed,
encrypted binaries on the SD card" and we'd rather keep the trust.
