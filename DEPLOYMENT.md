# Quadrant Parking – Deployment & Maintenance

## What runs where

| Part | Folder | Where it runs | Why |
|---|---|---|---|
| Website | `client/` | Vercel | Static React build |
| Main API (logins, bookings, slots) | `server/` | Render (or any Python host) | Light, always on |
| AI / CCTV engine | `backend/` | **Office PC on the camera network** | Needs the cameras (RTSP/ONVIF on the LAN) and 4–8 GB RAM for YOLO + OCR |
| Database | – | MySQL server reachable by both APIs (cloud MySQL or company server) | Single source of truth |

All data lives in **MySQL**: employees, slots, bookings, releases, notifications, **and** CCTV violations, their evidence photos (`cctv_violation_images`) and camera settings + slot layouts (`cctv_cameras`). The AI PC keeps nothing important locally, so it can be reinstalled or replaced at any time.

```
Phones / browsers ──HTTPS──> Vercel (website)
        │                        │
        ├──HTTPS──> Render: server/ (main API) ──┐
        └──HTTPS──> Cloudflare Tunnel ──> Office PC: backend/ (AI) ──┤──> MySQL
                                              │                        │
                                       CCTV cameras / NVR (LAN)  ──────┘
```

## 1. Database (once)

1. In MySQL Workbench run `server/db/mysql_updates.sql`:
   - **PART 1** (read-only checks) – review the output.
   - **PART 2–4** optional: rename `106A` slots, unique slot names, speed indexes.
   - **PART 6**: create the `parking_app` user (strong password).
   - **PART 7**: create the CCTV tables (`cctv_violations`, `cctv_violation_images`, `cctv_cameras`).
   Nothing in the script drops or alters existing tables; PART 5 undoes 2–4.
2. MySQL must accept remote connections (`bind-address = 0.0.0.0`, firewall port 3306 open only to the two servers).

Connection string used by **both** APIs:
```
DATABASE_URL=mysql+pymysql://parking_app:<password>@<mysql-host>:3306/parking_db
```
Special characters in the password must be URL-encoded (`@` → `%40`, `#` → `%23`).

## 2. Main API – `server/` on Render

Environment variables (Render dashboard → Environment):
```
DATABASE_URL=mysql+pymysql://...
JWT_SECRET_KEY=<long random secret – SAME value on the AI server>
SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_FROM_EMAIL
DEV_MODE=false
ALLOWED_ORIGINS=https://<your-vercel-domain>
FIREBASE_CREDENTIALS_PATH=<path, if push notifications are used>
```
Build: `pip install -r requirements.txt` · Start: `uvicorn main:app --host 0.0.0.0 --port $PORT` (already in `render.yaml`).

## 3. AI / CCTV engine – `backend/` on the office PC

1. Install Python 3.12, then `pip install -r backend/requirements.txt`.
2. Create `server/.env` on that PC (the AI engine reads it) with the **same** `DATABASE_URL` and `JWT_SECRET_KEY`.
3. Start: `cd backend && python -m uvicorn main:app --host 0.0.0.0 --port 8001`
   (no `--reload` in production). Make it start with Windows: Task Scheduler → "At startup", or NSSM as a Windows service.
4. Expose it over HTTPS (the website is HTTPS, so plain `http://` is blocked by browsers):
   `cloudflared tunnel --url http://localhost:8001` (or a named tunnel on e.g. `ai.parking.yourdomain.com`).
5. Health check: `https://<ai-address>/` → `{"status":"online"}`; logged in as admin: `/api/parking/system/status` → `"connected": true`.

Optional accuracy boost: put a YOLO licence-plate model at `backend/license_plate_detector.pt`.

## 4. Website – `client/` on Vercel

Environment variables, then redeploy:
```
VITE_API_URL=https://<render-api>/api
VITE_AI_API_URL=https://<ai-address>
```

## 5. Android APK

`android/gradle.properties` → `parkingWebUrl=https://<your-vercel-domain>`, then Android Studio → Build → Build APK(s).
Add `android/app/google-services.json` for push notifications (optional).

## Maintenance

| Task | How often | How |
|---|---|---|
| Database backup | Daily | `mysqldump --single-transaction parking_db > parking_YYYYMMDD.sql` (scheduled task); keep 30 days |
| Violation photo housekeeping | Monthly | Example at the end of `mysql_updates.sql` (keeps 1 year) |
| AI engine health | Daily glance | Admin → CCTV & AI shows "AI engine online · X/Y cameras live" |
| Camera offline | When it happens | Live monitor shows the error; check power/network, then Cameras → Stop/Start |
| OS / driver updates on the AI PC | Monthly | Restart afterwards; the engine reconnects all cameras on start |
| Updating code | Per release | `git pull` on the AI PC → `pip install -r backend/requirements.txt` → restart the engine. Render and Vercel redeploy on push |
| Slot changes | When needed | Create/rename slots only in **Admin → Parking Lots**; then match them in **CCTV → Slot layout** |

## Local development

`run_all.bat` starts all three (ports 8000, 8001, 5173). Run it **once** – if a window is left over from a previous run, close it first, otherwise two servers share a port and requests hang.
