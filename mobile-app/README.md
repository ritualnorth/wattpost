# WattPost mobile (Capacitor shell)

Native iOS + Android shell that wraps the existing `wattpost.cloud` PWA.
Strategy: thin native chrome, web everything else. The shell adds APNs/FCM
push, splash, status-bar tinting, safe-area handling, and native back/share.
The dashboard, sign-in, account, and alerts UI are rendered by the cloud
inside the WebView — zero duplication.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Native shell (iOS + Android)                       │
│   - Splash + StatusBar + safe-area insets           │
│   - APNs/FCM push receipt + deep-link               │
│   - Capacitor plugins: App, Browser, Preferences    │
├─────────────────────────────────────────────────────┤
│  www/ — bootstrap                                   │
│   index.html → bootstrap.js → window.location =     │
│     https://wattpost.cloud/app?from=mobile          │
├─────────────────────────────────────────────────────┤
│  Embedded WebView                                   │
│   Loads wattpost.cloud — same auth chain (cookie),  │
│   same SvelteKit/Litestar PWA users see in Safari   │
│   or Chrome. Subscription managed at wattpost.cloud │
│   only (Option B — no IAP, anti-steering-safe).     │
└─────────────────────────────────────────────────────┘
```

## v1 scope

- Sign in via cloud's existing email/password (Hobby users get the upgrade-gate screen).
- Multi-site picker → per-site broker dashboard, identical to PWA.
- APNs/FCM push for `Alerts` — fires off the existing VAPID infra in cloud.
- Account screen: minimal (signed-in email, manage-account link to Safari, sign out).
- App icon + splash from `www/assets/icon.svg` + `www/assets/splash.svg`.

Out of scope for v1: native dashboard reimplementation, BLE pairing on phone,
Watch / CarPlay / widgets. See `~/.claude/projects/-home-james-solar-monitor/memory/project_wattpost_mobile_app.md`
for the full scope decision log.

## Local development (web bootstrap only)

```bash
npm install
# Static www/ — no bundler. Open www/index.html in a browser to test
# the bootstrap UX (won't actually redirect cleanly without the native
# shell because the cloud will set cookies for the wrong origin).
```

## Adding native targets

Done **once**, then checked in.

### iOS — requires macOS + Xcode 15+

```bash
# On a Mac with Xcode + CocoaPods:
cd mobile-app
npm install
npx cap add ios
npx cap sync ios
npx cap open ios   # opens Xcode

# In Xcode:
# 1. Signing & Capabilities → Team: WattPost Ltd (need Apple Dev account)
# 2. Add capability: Push Notifications
# 3. Add capability: Background Modes → Remote notifications
# 4. Set Bundle Identifier: io.wattpost.app
# 5. Configure App Icon set from www/assets/icon.svg (use Xcode's
#    AppIcon asset catalog or `npx cordova-res ios` to generate sizes)
```

### Android — DONE on the dev laptop (2026-05-21)

The Android target is scaffolded and builds a working debug APK on
this dev laptop (192.168.1.13, Ubuntu 24.04). Toolchain installed
under `~/Android/Sdk`; env vars persisted in `~/.bashrc`.
Requirements: OpenJDK 21 + Android SDK cmdline-tools + platforms;android-34
+ build-tools;34.0.0. Capacitor 8 needs JDK 21 (not 17).

Build the debug APK:

```bash
cd /home/user/solar-monitor/mobile-app
npm run sync                        # cap sync + copies www/ in
cd android
./gradlew assembleDebug
# Output: android/app/build/outputs/apk/debug/app-debug.apk (~5.6 MB)
```

Sideload to a real device for testing:

```bash
adb devices                         # device must show as authorised
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

For a signed release APK (Play Store), generate a keystore + add
to `android/app/build.gradle`:

```bash
keytool -genkey -v -keystore ~/wattpost-release.keystore \
  -alias wattpost -keyalg RSA -keysize 2048 -validity 10000
./gradlew bundleRelease
# Output: android/app/build/outputs/bundle/release/app-release.aab
```

Configure firebase-messaging for FCM push when the Firebase project
lands (separate setup, see below).

## Push notifications

Both platforms register a device token on app launch. Token is POSTed to
`POST https://wattpost.cloud/api/mobile/push/register` (cloud-side route
TODO before app-store submission). Cloud stores token alongside user_id +
device label and uses it when an alert fires for any of the user's
appliances.

iOS uses APNs. Android uses FCM. Cloud needs:
- Apple APNs auth key (.p8) from Apple Developer Console
- Firebase project + service account JSON for FCM

Both secrets paste into the VPS at `/opt/wattpost-cloud/secrets/` —
never commit either.

## App Store / Play Store submission

See `STORE-SUBMISSION.md` (TODO — write before first submission).

## Costs

- Apple Developer Program: £79/yr (one-time signup, ~24h verification)
- Google Play Developer: £25 one-off (~24h verification)

## Why Capacitor (decision log)

Picked over Tauri Mobile (2.x beta on mobile, would block first release),
React Native (full rewrite of every screen), Flutter (Dart rewrite), and
pure PWA (no iOS App Store path). Capacitor wraps the existing PWA with
~95% code reuse. TypeScript matches the rest of the WattPost stack. Ionic
Inc. backs it commercially.

Full decision context in the memory file referenced above.
