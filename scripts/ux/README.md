# UX audit

`audit.mjs` drives a running WattPost instance with Playwright, screenshots the main pages
and each Settings section (desktop and phone, light and dark) and measures: text contrast,
unlabelled controls, touch-target size, smallest font, heading structure and horizontal
overflow. It is the before/after check for UI work (see `docs/design/ui-ux-remediation.md`).

```sh
# a throwaway instance with its own password directory
export WATTPOST_PASSWORD_DIR=/tmp/ux/pw WATTPOST_KEYS_DIR=/tmp/ux/keys
python -c "from solar_monitor import web_auth as wa; wa.write_password_hash('ux-pass')"
python -m solar_monitor.cli serve --config config.example.yaml --db /tmp/ux/db.sqlite --port 8421 &

NODE_PATH=$(npm root -g) node scripts/ux/audit.mjs --base http://127.0.0.1:8421 \
    --password ux-pass --subs devices,alerts,integrations,backup,privacy,advanced,about --out /tmp/ux-after
```

Contrast is computed from computed styles, so gradients and translucent backgrounds can make
it approximate. Treat it as a ranking, not a verdict.

## Checking a CSS-only refactor

`ab.mjs` loads every page twice in one browser session, once with stylesheet A and once with B
(swapped in by request interception), plus a second A as a control, so HTML, JS and live data are
identical. Animated elements (the donut rings, the status dot) and changing numbers still differ
between any two loads, so compare A-vs-B against A-vs-A rather than expecting zero.

```sh
git show main:solar_monitor/web/styles.css > /tmp/old.css
NODE_PATH=$(npm root -g) node scripts/ux/ab.mjs --password ux-pass --css-a /tmp/old.css \
    --css-b solar_monitor/web/styles.css --out /tmp/ux-ab
python3 scripts/ux/compare.py /tmp/ux-ab/a /tmp/ux-ab/b      # and /tmp/ux-ab/a /tmp/ux-ab/a2 for the noise floor
```
