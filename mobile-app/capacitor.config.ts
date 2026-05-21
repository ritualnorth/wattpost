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
  },
  android: {
    backgroundColor: '#0d1117',
    allowMixedContent: false,
    captureInput: true,
    webContentsDebuggingEnabled: false,
  },
  server: {
    // The native shell ships a bootstrap www/ that immediately hands
    // off to the cloud broker. We DON'T point server.url at the cloud
    // (that loses the ability to ship offline-fallback later + breaks
    // Apple-review demo mode). Bootstrap loads, then redirects.
    androidScheme: 'https',
    iosScheme: 'https',
    cleartext: false,
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
