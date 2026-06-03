# Mobile apps and home-screen install

There are **three** ways to put WattPost on your phone, and the differences matter for push notifications. Pick the one that fits.

## Quick decision

| You want… | Use this |
|---|---|
| Notifications when an appliance fires an alert | **Native app** (Android / iOS Coming Soon), or the home-screen install below |
| A WattPost icon on your home screen, no app store | **Add to Home Screen from wattpost.cloud/app** |
| Fast lookup from a desktop browser | A regular browser bookmark to `wattpost.cloud/app` |

Whatever you do, **always install from `wattpost.cloud/app`**, never from the per-appliance URL (`abc123xyz0.wattpost.io/` etc). The reason's [below](#why-not-the-broker-url).

## Option 1 · Native app (recommended on phone)

The WattPost app is on Google Play (iOS coming soon, see [project status](https://wattpost.cloud/blog) for the App Store timeline).

It uses **Apple's APNs / Google's FCM** native push, so notifications arrive even with the app force-closed and the phone screen off. That's the highest-reliability path for alerts.

It's a thin wrapper around the same `wattpost.cloud/app` web app you'd use in a browser, so your dashboard, sites list, alerts, and account all behave identically.

## Option 2 · Add to Home Screen (PWA)

If you don't want to install the app from the store, or you're on iOS while we're still cooking the App Store build, you can pin WattPost to your home screen straight from Safari / Chrome.

**Steps (iOS):**
1. Open `https://wattpost.cloud/app` in Safari
2. Sign in
3. Tap the **Share** icon (the square with the arrow)
4. Scroll → tap **Add to Home Screen**
5. Tap **Add**

**Steps (Android Chrome):**
1. Open `https://wattpost.cloud/app` in Chrome
2. Tap the **⋮** menu
3. Tap **Install app** (or **Add to Home Screen** depending on Chrome version)

You'll get a WattPost icon on your home screen. Tapping it opens the dashboard in a full-screen app frame, no browser tabs, no URL bar.

### Push notifications via PWA

Once you've added to home screen, **open the PWA from the home-screen icon** (not from Safari), then go to **Account → Notifications → Enable on this browser**.

Push will work for any of your appliances. The browser delivers it via Apple's web-push service (iOS) or Google FCM (Android), same channel as a native app for delivery reliability, but it needs the PWA to be installed first (iOS especially blocks `PushManager` outside home-screen installs).

## Option 3 · Regular browser tab

Just open `wattpost.cloud/app` in any browser. Bookmark it if you want.

You can still receive push notifications without installing, same Enable button on **Account → Notifications**, they'll arrive while the browser is running, even if the WattPost tab isn't open. **On iOS Safari this only works after Add to Home Screen.** Other browsers (Chrome / Firefox on desktop, Chrome on Android) work without an install.

## Why not the broker URL? {#why-not-the-broker-url}

Each appliance has its own remote-access URL, `abc123xyz0.wattpost.io/`, `def456uvw0.wattpost.io/`, etc. These open the appliance's local dashboard through a Cloudflare tunnel.

You **can** browse them, but you should not **install** them as a PWA.

Push notifications register against the page's origin (the part of the URL before the first `/`). If you install a PWA from `abc123xyz0.wattpost.io/`:

- The PWA only knows about that one appliance
- Push registers against that subdomain, but cloud-fired alerts (like "appliance offline") are sent from `wattpost.cloud`'s origin
- Notifications don't deliver to the broker PWA
- There's no multi-site picker
- There's no alerts inbox
- The account / billing pages aren't reachable inside the PWA

Install from `wattpost.cloud/app` instead. From there you tap into any site to see its dashboard, including the same broker view, but inside the WattPost app shell with the alerts inbox, account, and the rest of your fleet one tap away.

(As of `v0.1.43` of the appliance, broker URLs explicitly suppress the "Add to Home Screen" prompt to keep this from happening by accident. On LAN, `192.168.x.x`, the appliance's own PWA still installs, for the offline-first / no-cloud user.)

## Notifications screen explained

`/app/account/notifications` lists every device that's been enabled to receive WattPost push. Each row has a channel chip:

- **APP · APNS** (blue, native iOS app)
- **APP · FCM** (blue, native Android app)
- **HOME-SCREEN** (green, iOS Safari PWA)
- **BROWSER** (grey, desktop or Android browser)

If a device shows up that you don't recognise, or you've replaced a phone, tap **Revoke**. Future alerts won't deliver to it.

## What can fire push notifications

Three kinds of events can land on your phone:

1. **Smart-scene rule** you've defined, e.g. *"SoC below 20% → push me"*. Configure these at `/app/rules`. Each rule has its own cooldown so you don't get spammed.
2. **Appliance offline**, fires once when the appliance stops sending heartbeats for longer than the configured threshold (default 15 min). Re-fires once when it comes back. No bombardment.
3. **Local alerts** fired by the appliance and uploaded, these *land in the alerts inbox at `/app/alerts`* but are not currently auto-pushed. Configure a Smart-scene rule against the `Active alerts > 0` metric if you want push.

## Privacy

Push delivery uses Apple's APNs, Google's FCM, and the browser vendor's web-push relay. WattPost only knows whether a push request succeeded, we never see the rendered notification on your device. Revoking a device removes its endpoint from our database immediately; soft-delete keeps the audit trail but the endpoint isn't called again.
