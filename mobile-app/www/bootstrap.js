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

    const base = await pickBaseUrl();
    // Hand off to the cloud /app entry. The cloud renders sign-in or
    // the multi-site picker depending on session state.
    const target = `${base}/app?from=mobile`;
    msg.textContent = 'Loading WattPost…';
    // Small delay so the splash has time to fade — feels less janky
    // than an instantaneous redirect.
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
