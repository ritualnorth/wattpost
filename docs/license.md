# License

WattPost is **open source**, licensed under **Apache License 2.0**. The
appliance source ships with every SD image (`/opt/wattpost-src/`) and
every Docker image (`/app/solar_monitor/`), so you can audit and modify
exactly what runs on your hardware.

## Plain-English summary

- ✅ Audit the code, read every line, run a debugger through it.
- ✅ Modify your own copy. Add a vendor driver, change a UI string,
  swap a colour, fork it for anything you like.
- ✅ Run it on your own hardware (Raspberry Pi, mini PC, RV, boat, off-grid cabin).
- ✅ Use it commercially — build a product on it, offer it as a hosted
  service, white-label it. Apache 2.0 permits this.
- ✅ Share patches back. Pull requests welcome.
- ➡️ If you redistribute it (modified or not), keep the `LICENSE` and
  `NOTICE` files and the existing copyright notices, and note any
  significant changes you made. That's the main obligation.
- ❌ Use the "WattPost" name or logo for anything other than referring
  to this project. Apache 2.0 grants copyright + patent rights, **not**
  trademark rights — the name and logo are ours.

## The formal terms

Licensed under [**Apache License 2.0**](https://www.apache.org/licenses/LICENSE-2.0).
The full text is in [LICENSE](../LICENSE), with attributions in
[NOTICE](../NOTICE), bundled with every distribution. Apache 2.0 is a
widely-used, permissive, OSI-approved licence with an explicit patent
grant.

## The cloud tier

The code at `wattpost.cloud` (multi-site dashboard, heartbeat ingest,
Cloudflare tunnel provisioning, REST API server, billing) is **not**
distributed and **not** covered by this licence. It's private to
Ritual North. This licence covers the appliance in this repository only.

## Why open source

- **Trust:** You can verify there's no spyware / phone-home / cloud
  lock-in. The source on your Pi *is* what's running.
- **Repairability:** A vendor going out of business shouldn't brick
  your batteries. With the source, you can keep WattPost alive even if
  we disappear.
- **Longevity:** A permissive licence means the work can outlive us —
  anyone can pick it up, fork it, ship it, or build on it without
  asking permission.
