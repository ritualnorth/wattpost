# UI and UX remediation: scope

Status: in progress. Driven by the October 2026 front-end review (menu, Settings, stylesheets).
Measured with `scripts/ux/audit.mjs` against the live appliance UI, light and dark, desktop (1440)
and phone (390).

## Baseline (before)

- Text contrast: about 40% of text nodes on the Settings pages, and 40% on the dashboard and History, fall below 4.5:1 (light), similar in dark. Cause: two muted-text colour tokens and the inactive nav colour.
- Unlabelled controls: 8 sliders/number boxes on Alerts, the kiosk-skin select, the update-channel select.
- Headings: no `h1` on any view, heading skips on Alerts and Backup.
- Phone: tab labels 9.9px; 3 to 28 touch targets under 44px per page.
- Settings: Advanced is a 3,450px page with nine unrelated sections; two things called "Devices".
- CSS: 63 font sizes, 186 spacing values, 19 radii, 191 literal colours, 67 `!important`, 11 z-index values, 42 unused classes, dead Settings dropdown code, 193 inline styles in `index.html`.

## Targets (after)

| Measure | Target |
|---|---|
| Low-contrast text nodes | under 5% on every page, both themes; every text token pair at least 4.5:1 |
| Unlabelled controls | 0 |
| `h1` per view | exactly 1; no heading skips |
| Phone touch targets under 44px | 0 for stand-alone controls (inline links in prose excepted) |
| Smallest text on phone | 11px or more |
| Horizontal overflow | none |
| Settings page height (desktop) | no section page over about 1,800px |
| Font sizes | from a defined scale only |
| Literal colours outside tokens | under 30, each justified |
| `!important` | under 20 |
| Unused classes | 0 |

## Settings information architecture

Nine sections, ordered by how often people need them.

| Section | Holds |
|---|---|
| Add devices | what "Settings, Devices" is today (pairing Renogy/Victron/BMS gear) |
| Alerts | quiet hours, rules, transports |
| Display | appearance (theme), kiosk default and skin |
| Network | WiFi join, hotspot, network security (firewall, SSH) |
| Integrations | Solcast, weather, MQTT, Prometheus, WattPost cloud (cloud first) |
| Privacy and sharing | web password, kiosk links, location, discovery, install ping |
| Backup and restore | unchanged |
| Advanced | history and polling (with the duplicate Retention card merged in), diagnostics |
| About | system status, device health, session, version and updates, reset to defaults |

Old links keep working: `#/settings/advanced` stays valid; anchors that moved redirect to their new section.

## Design tokens

- Type: `--fs-2xs` 11px, `--fs-xs` 12, `--fs-sm` 13, `--fs-md` 14, `--fs-base` 16, `--fs-lg` 18, `--fs-xl` 22, `--fs-2xl` 28. Dashboard display numerals keep their own sizes.
- Space: `--sp-1` 4px, `--sp-2` 8, `--sp-3` 12, `--sp-4` 16, `--sp-5` 24, `--sp-6` 32, `--sp-8` 48.
- Radius: `--r-xs`, `--r-sm`, `--r`, `--r-lg`, `--r-pill`. Weights: 400, 500, 600, 700. z-index: a named scale.
- Native `@layer` order: tokens, base, layout, components, pages, utilities. No build step.

## Phases (each its own PR, merged on green CI)

1. Accessibility quick wins: contrast tokens, labels, headings, focus ring, touch targets, tab labels, demo badge.
2. Delete dead CSS and JS.
3. Settings information architecture.
4. Action affordances and form controls.
5. Split `styles.css` into layered files.
6. Type, space, radius, weight and z-index tokens; literal colours to tokens.
7. Repeated inline styles to classes.
8. Guard tests in CI so none of it regresses.
9. Cloud: re-vendor the UI, token parity test, Manage sheet sections.

Every phase bumps the service-worker cache version and the `?v=` cache-busters, keeps the
vendored copy in the cloud in sync, and is checked with before/after audit runs and screenshots.

## Out of scope

A framework or component library, a redesign of the dashboard or charts, new features.
