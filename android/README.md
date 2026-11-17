# Android app — Quadrant Parking

**Package / applicationId:** `com.quadrantitservices.parking`

This is a WebView shell around the React parking website. Use this same package name in Firebase when you add push notifications later.

## Open in Android Studio

1. Open Android Studio → **Open** → select this folder:
   `QITS-Parking-main/android`
2. Wait for Gradle sync.
3. Set the website URL (see below).
4. Run on emulator or phone → Build → Build APK(s).

## Set website URL

Edit `app/build.gradle.kts`:

| Build | URL |
|--------|-----|
| **debug** (emulator) | `http://10.0.2.2:5173` (Vite on your PC) |
| **debug** (real phone) | `http://YOUR_PC_IP:5173` (same Wi‑Fi) |
| **release** | your live HTTPS site, e.g. Vercel URL |

Also start the web app:

```bash
cd client
npm run dev -- --host
```

(`--host` lets the phone reach your PC.)

## Firebase push notifications (Android APK)

### Already wired
- FCM service shows system notifications
- Asks notification permission (Android 13+)
- Gets FCM token and exposes it to the website (`ParkingApp.getFcmToken()`)
- After login, website registers token at `POST /api/devices/register`
- When a manager reclaims a slot, server sends FCM to that employee

### You must still do
1. **Service account** (server send key)  
   Firebase Console → Project settings → Service accounts → Generate new private key  
   Save as e.g. `server/firebase-service-account.json`  
   In `server/.env`:
   ```env
   FIREBASE_CREDENTIALS_PATH=./firebase-service-account.json
   ```
2. Install backend deps: `pip install firebase-admin`
3. Point APK `WEB_APP_URL` at your live HTTPS site (same API the web uses)
4. Rebuild APK, allow notifications when prompted, **log in once**

### Quick test without server
Firebase Console → Messaging → Send test message → paste device token from Logcat (`FCM token ready`).
