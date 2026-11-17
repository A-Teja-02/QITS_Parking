/**
 * FCM token helpers for Android WebView APK (window.ParkingApp / __FCM_TOKEN__).
 */

declare global {
  interface Window {
    ParkingApp?: {
      getFcmToken?: () => string;
      isNativeApp?: () => boolean;
    };
    __FCM_TOKEN__?: string;
  }
}

export function getNativeFcmToken(): string {
  try {
    if (typeof window === 'undefined') return '';
    if (window.ParkingApp?.getFcmToken) {
      const t = window.ParkingApp.getFcmToken();
      if (t && t.length > 20) return t;
    }
    if (window.__FCM_TOKEN__ && window.__FCM_TOKEN__.length > 20) {
      return window.__FCM_TOKEN__;
    }
  } catch {
    // ignore
  }
  return '';
}

export function isNativeAndroidApp(): boolean {
  try {
    return !!window.ParkingApp?.isNativeApp?.();
  } catch {
    return false;
  }
}
