# Parking Reservation System

An internal company parking reservation application built with a FastAPI (Python) backend and a React (TypeScript) + Vite frontend.

## Features
- **Interactive Parking Map**: View and reserve parking spots in real-time.
- **Admin Dashboard**: Manage floors, slots, users, and reservations.
- **Role-based Access**: Custom interfaces for Employees, Managers, and HR/Admin.
- **Automatic Releases**: Managers can release their assigned slots for others to book.

## Project Structure
- `/client`: Frontend codebase (React, Vite, Tailwind CSS / Vanilla CSS).
- `/server`: Backend API (FastAPI, SQLite / local JSON database).
- `render.yaml`: Configuration for deploying the backend service to Render.

## Getting Started

### Backend Setup
1. Navigate to the `server` directory:
   ```bash
   cd server
   ```
2. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Seed the database (optional):
   ```bash
   python seed.py
   ```
5. Start the FastAPI server:
   ```bash
   uvicorn main:app --reload
   ```

### Frontend Setup
1. Navigate to the `client` directory:
   ```bash
   cd client
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the development server:
   ```bash
   npm run dev
   ```

## CCTV slot monitoring (Admin → CCTV & AI)

The AI backend (`/backend`, port 8001) watches parking slots through RTSP cameras and flags vehicles parked in a slot they did not book.

1. **Cameras** – add a camera by RTSP URL or by brand + IP + login (Hikvision, Dahua, CP Plus, …). *Test connection* shows a live preview. Use the main stream for best plate reading.
2. **Slot layout** – grab a live frame and outline the slots this camera covers. *Draw row* splits a whole row into N slots with perspective; *Draw slot* adds single slots. Use the real slot IDs (P101…) so bookings can be checked, then *Save layout*.
3. **Live monitor** – the server analyses only those slots, continuously (the page does not need to stay open). Each slot shows occupancy, the plate, who booked it and who owns the car.
4. **Violations** – when a car stays parked (default 20 s) in a slot booked for someone else, or without a booking, a violation is saved with the scene photo, vehicle photo and plate crop, the owner's details (if the plate is a registered company vehicle) and who the slot was for. HR admins get an in-app notification (and email, if SMTP is set in `server/.env`).

**Photo test (no camera needed):** upload or take a photo, press *Detect slots* (finds bays from yellow / blue / white floor lines, or around parked cars), check the slot IDs, then *Check parking*. Violations show the captured number plate (tap to enlarge), the vehicle, the owner and who booked the slot; with *Save violations to log* on they also appear in the Violations tab. For a close-up of one car use *Whole photo = 1 slot*.

Accuracy tips:
- Mount cameras so plates are at least ~100 px wide in the image; avoid strong backlight.
- Outline slots on the floor where the tyres sit, not around the car roof.
- When the slots are empty, press *Capture now* (empty-slot baseline) in Live monitor.
- For much better plate detection, put a YOLO licence-plate model at `backend/license_plate_detector.pt` (or set `ANPR_PLATE_MODEL`); it is picked up automatically.

## Deployment

Refer to the [Deployment Guide](deployment_guide.md) for full instructions on deploying the backend to Render and the frontend to Vercel.

## MySQL, ONVIF and production notes

- **Database:** both servers use `DATABASE_URL` from `server/.env`. For MySQL:
  `DATABASE_URL=mysql+pymysql://parking_app:<password>@<mysql-host>:3306/parking_db`.
  The AI/CCTV engine reads bookings, releases, employees and slot names from the same database
  (check: `GET /api/parking/system/status`). Schema updates for MySQL: `server/db/mysql_updates.sql`.
- **Slot names** are unique (case/space-insensitive); the API rejects duplicates.
- **ONVIF:** Admin → CCTV & AI → Cameras → Add camera → "ONVIF (find automatically)". The AI server must be on the camera network and ONVIF enabled on the camera/NVR.
- **Security:** all `/api/parking` (CCTV/AI) endpoints require an HR/Admin login.
- **Production:** set `VITE_AI_API_URL` (e.g. `https://ai.example.com`) when building the client if the AI backend runs on a different address.
