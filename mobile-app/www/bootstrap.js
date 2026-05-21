// WattPost mobile bootstrap.
//
// The native shell loads this page on launch. It:
//   1. Decides which cloud env to point at (prod by default, configurable
//      for review/staging).
//   2. Hands off to the cloud WebView at <env>/app — auth chain stays
//      in the cookie/session jar the WebView owns. No tokens cross
//      back into the native shell for v1.
//
// We keep this thin on purpose. Anything richer (sign-in form, push prefs,
// account screen) is rendered server-side by wattpost.cloud and shown
// inside the same WebView. The native shell just adds:
//   - APNs/FCM push receipt + deep-linking back to /app
//   - StatusBar tinting + safe-area inset coverage
//   - Splash + first-launch entitlement prompts

const ENV = {
  prod: 'https://wattpost.cloud',
  staging: 'https://staging.wattpost.cloud',
  // local dev: simulator/emulator only — `host.docker.internal` style
  // doesn't work cleanly on iOS, set via Preferences before launch
  // when needed.
  dev: 'http://localhost:8080',
};

const msg = document.getElementById('msg');

async function pickBaseUrl() {
  // In v1, every shipped build is prod. Staging/dev is opt-in via a
  // long-press gesture on the splash logo (TODO once we need it).
  return ENV.prod;
}

// Register for push notifications on the LOCAL Capacitor origin
// before redirecting to wattpost.cloud. The cloud page can't reach
// the PushNotifications plugin (cross-origin webview limitation —
// Capacitor.Plugins proxy isn't initialised on external origins),
// so we register here, wait for the FCM token, then pass it forward
// as a URL param. The cloud reads ?fcm= on /app and POSTs to
// /api/account/push/mobile/register once authenticated.
//
// Times out after 4 s — if the device can't reach FCM (no Play
// services, no network, etc.) we still let the user into the app;
// push just won't work this session.
async function registerForPushAndGetToken() {
  const PN = window.Capacitor?.Plugins?.PushNotifications;
  if (!PN) return null;
  try {
    const perm = await PN.requestPermissions();
    if (perm.receive !== 'granted') return null;
    // Create our "alerts" channel so the cloud's FCM payload
    // (which sets channel_id="alerts") can land on a HIGH-importance
    // channel instead of FCM's lowercased fallback. No-op on iOS.
    try {
      if (window.Capacitor.getPlatform && window.Capacitor.getPlatform() === 'android') {
        await PN.createChannel({
          id: 'alerts',
          name: 'Alerts',
          description: 'Battery, charger, and device alerts',
          importance: 5,    // HIGH — heads-up banner + sound
          visibility: 1,    // PUBLIC — show on lockscreen
          lights: true,
          vibration: true,
        });
      }
    } catch (e) { console.warn('bootstrap push: createChannel failed', e); }
    await PN.register();
  } catch (e) {
    console.warn('bootstrap push: register call failed', e);
    return null;
  }
  return new Promise((resolve) => {
    let done = false;
    const settle = (v) => { if (!done) { done = true; resolve(v); } };
    PN.addListener('registration', (t) => settle(t && t.value || null));
    PN.addListener('registrationError', (e) => {
      console.warn('bootstrap push: registrationError', e);
      settle(null);
    });
    setTimeout(() => settle(null), 4000);
  });
}

// If the user opened the app by tapping a push notification, the
// Capacitor PushNotifications plugin queues a tap event that fires
// shortly after a listener is registered. We catch it here and
// override the default /app redirect with the URL the cloud baked
// into the FCM data block (e.g. /app/sites/12 for a per-site alert).
function setupTapNavigationListener(base) {
  const PN = window.Capacitor?.Plugins?.PushNotifications;
  if (!PN) return Promise.resolve(null);
  return new Promise((resolve) => {
    PN.addListener('pushNotificationActionPerformed', (event) => {
      const url = event && event.notification && event.notification.data && event.notification.data.url;
      if (url && typeof url === 'string') {
        // Same-origin path → prepend base; absolute → use as-is.
        const target = url.startsWith('http') ? url : `${base}${url}`;
        resolve(target);
      }
    });
    // No-tap path resolves null after a short window; the bootstrap's
    // regular redirect runs.
    setTimeout(() => resolve(null), 250);
  });
}

async function go() {
  try {
    // Push the WebView below the system status bar. Without this,
    // Android draws the WebView edge-to-edge AND we hit a quirk
    // where env(safe-area-inset-top) reports 0, so even with our
    // CSS fix the topbar overlaps the clock/battery icons.
    // Setting overlay=false makes the status bar its own region.
    try {
      if (window.Capacitor?.Plugins?.StatusBar) {
        await window.Capacitor.Plugins.StatusBar.setOverlaysWebView({ overlay: false });
      }
    } catch (e) { /* PWA path / pre-init — ignore */ }

    // Kick off push registration in parallel with everything else.
    // We don't block the redirect on it — if the token arrives in
    // time, we pass it through; if not, the next launch picks it up.
    const tokenPromise = registerForPushAndGetToken();

    const base = await pickBaseUrl();
    msg.textContent = 'Loading WattPost…';

    // If launched from a tap, snag the deep-link URL.
    const tapTargetPromise = setupTapNavigationListener(base);

    // Wait briefly for the FCM token AND any pending tap-navigation.
    const [token, tapTarget] = await Promise.all([
      Promise.race([
        tokenPromise,
        new Promise((r) => setTimeout(() => r(null), 3000)),
      ]),
      tapTargetPromise,
    ]);
    const platform = (window.Capacitor && window.Capacitor.getPlatform &&
                      window.Capacitor.getPlatform()) || 'web';

    // Build the redirect. Default is /app; if the user tapped a push,
    // route them straight to the URL the cloud baked into the payload.
    const params = new URLSearchParams({ from: 'mobile', platform });
    if (token) params.set('fcm', token);
    const sep = (tapTarget && tapTarget.includes('?')) ? '&' : '?';
    const target = tapTarget
      ? `${tapTarget}${sep}${params.toString()}`
      : `${base}/app?${params.toString()}`;
    setTimeout(() => { window.location.replace(target); }, 200);
  } catch (e) {
    msg.classList.add('err');
    msg.textContent = 'Cannot reach WattPost. Check your connection.';
    console.error('bootstrap failure', e);
  }
}

// Defer until DOM is fully ready (it is, but be defensive about the
// Capacitor StatusBar plugin which mutates the viewport on iOS).
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', go, { once: true });
} else {
  go();
}
