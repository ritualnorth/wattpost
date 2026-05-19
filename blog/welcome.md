# Welcome to the WattPost blog

WattPost is a local-first solar monitor for off-grid rigs that
don't fit any one vendor's app. The product is a Raspberry Pi (or
any Linux box) that talks Modbus to Renogy gear, decodes Victron
BLE Instant Readout broadcasts, and reads JK BMS adverts. One
dashboard. No cloud account required.

We started building it because every off-grid setup we've seen
runs two or three separate vendor apps, none of which talk to
each other. Most of those apps insist on a cloud account before
they show you your own battery. That's not the world we want to
build for.

This blog covers three kinds of post.

**Release notes.** Plain English summaries of what shipped in
each version and why. Cross-linked to the canonical changelog.

**Tutorials.** End-to-end walkthroughs. First install, wiring
options, integrations with Home Assistant or Grafana, smart-scene
rules. Each one is written so you can flash an SD card on a
Thursday evening and have live battery telemetry on the dashboard
before bed.

**Debugging write-ups.** When we get stuck and figure something
out, we write it down. Last week we lost most of a day to a BT-2
dongle that was paired to a laptop on the same network. The
software side was clean. The fix was physical. That kind of story
saves the next person a day.

Posts here earn their place by being useful to somebody flashing
their first SD card, comparing off-grid monitors, or debugging the
same problem we already solved. We are not going to write filler
for the sake of cadence.

The first proper tutorial is in production now. Working title:
*From blank SD card to live dashboard, in one sitting*. Video at
every step, screenshots where they help, no skipped detail. It is
the kind of content the install path deserves and the kind of
content we wished was on every other monitor's site when we
evaluated them.

Until then, the [supported hardware](/docs/supported-hardware)
page lists everything WattPost talks to today plus what is in the
queue. If you've got a setup that isn't covered, or a vendor you
wish was supported, email
[support@wattpost.io](mailto:support@wattpost.io). We usually
reply within a day.
