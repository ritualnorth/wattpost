# Welcome to the WattPost blog

WattPost is a local-first solar monitor for the rigs that don't fit any one vendor's app — a Raspberry Pi (or any Linux box) talking Modbus to your Renogy gear, listening to Victron BLE Instant Readout broadcasts, and decoding JK BMS adverts, all on one dashboard. We started building it because every off-grid setup we've seen ends up running 2–4 vendor apps, none of which speak to each other, and most of which require a cloud account just to read your own battery.

This blog will cover three things: **release notes** (what shipped + why), **tutorials** (end-to-end walkthroughs — first install, wiring options, integrations), and **the occasional debugging war story** (last week we lost most of a day to a BT-2 dongle that turned out to be paired to a laptop on the same network — a one-master-at-a-time gotcha worth writing up properly).

We're not going to do build-in-public for its own sake. Posts here will earn their place by being useful — to somebody flashing their first SD card, somebody comparing monitoring options, or somebody debugging the same problem we already solved. If you want product news in your inbox, the monthly energy-summary email also carries release notes; if you want everything as it lands, this page + the RSS feed will be it.

First proper tutorial is in the oven now: **flashing the SD card, first-boot, pairing your gear, your first reading on the dashboard — end to end with video at every step.** That's the kind of content the install path deserves, and it's the kind of content we wished was on every other monitor's site when we evaluated them.

Until then — if you've got a setup you're curious about, or a vendor you wish was supported, the [supported hardware page](/docs/supported-hardware) lists everything we ship today and what's still in the queue. Email [support@wattpost.io](mailto:support@wattpost.io) and we'll usually have something to say within a day.
