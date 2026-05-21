import { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'io.wattpost.app',
  appName: 'WattPost',
  webDir: 'www',
  bundledWebRuntime: false,
  ios: {
    contentInset: 'always',
    backgroundColor: '#0d1117',
    scheme: 'WattPost',
    // Allow the app to load https://*.wattpost.cloud + wattpost.cloud
    // without HTTPS errors mid-handshake.
    limitsNavigationsToAppBoundDomains: false,
    // Marker the cloud sniffs in User-Agent to strip the marketing
    // chrome (topbar + footer) so /login + /app feel native instead
    // of "the website inside a frame". Bumped in lock-step with the
    // mobile-app/package.json version.
    appendUserAgent: 'WattPostApp/0.1.0',
  },
  android: {
    backgroundColor: '#0d1117',
    allowMixedContent: false,
    captureInput: true,
    webContentsDebuggingEnabled: false,
    appendUserAgent: 'WattPostApp/0.1.0',
  },
  server: {
    // The native shell ships a bootstrap www/ that immediately hands
    // off to the cloud broker. We DON'T point server.url at the cloud
    // (that loses the ability to ship offline-fallback later + breaks
    // Apple-review demo mode). Bootstrap loads, then redirects.
    androidScheme: 'https',
    iosScheme: 'https',
    cleartext: false,
    // Keep all wattpost.cloud navigation inside the in-app WebView.
    // Without this whitelist Capacitor sees the bootstrap's redirect
    // to an external origin and punts to the system browser — which
    // shipped the app straight to Chrome on first launch. The broker
    // sub-domains (<slug>.wattpost.cloud) need the wildcard.
    allowNavigation: [
      'wattpost.cloud',
      '*.wattpost.cloud',
    ],
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 800,
      backgroundColor: '#0d1117',
      androidSplashResourceName: 'splash',
      androidScaleType: 'CENTER_CROP',
      showSpinner: false,
      splashFullScreen: true,
      splashImmersive: true,
    },
    PushNotifications: {
      presentationOptions: ['badge', 'sound', 'alert'],
    },
    StatusBar: {
      style: 'DARK',
      backgroundColor: '#0d1117',
    },
  },
};

export default config;
