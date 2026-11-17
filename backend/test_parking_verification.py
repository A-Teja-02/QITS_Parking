import urllib.request
import json

base_url = "http://localhost:8001"

# 1. Test today's reservations
print("--- 1. Testing GET /api/parking/reservations/today ---")
try:
    with urllib.request.urlopen(f"{base_url}/api/parking/reservations/today") as res:
        print("Status:", res.getcode())
        data = json.loads(res.read().decode())
        print("Today's reservations count:", len(data))
        for r in data:
            print(f"  - Slot: {r.get('slot_id')} | User: {r.get('user_name')} | Plate: {r.get('vehicle_number')}")
except Exception as e:
    print("Error:", e)

# 2. Test simulation of wrong parking alert
print("\n--- 2. Testing POST /api/parking/anpr/test-violation-alert ---")
try:
    payload = {
        "slot_id": "P101",
        "detected_plate": "MH 12 CD 1234",
        "violation_type": "WRONG_SLOT",
        "expected_plate": "MH 14 AB 1111",
        "expected_user": "Admin User",
        "actual_driver": "Amit Kumar (Reserved for P103)"
    }
    req = urllib.request.Request(
        f"{base_url}/api/parking/anpr/test-violation-alert",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as res:
        print("Status:", res.getcode())
        print("Response:", res.read().decode())
except Exception as e:
    print("Error:", e)

# 3. Test slot verification endpoint with synthetic frame
print("\n--- 3. Testing POST /api/parking/anpr/verify-slot ---")
try:
    # Minimal 1x1 black pixel jpeg in base64
    tiny_b64 = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
    payload = {
        "image_base64": tiny_b64,
        "slot_id": "P101",
        "camera_id": "test_cam"
    }
    req = urllib.request.Request(
        f"{base_url}/api/parking/anpr/verify-slot",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as res:
        print("Status:", res.getcode())
        data = json.loads(res.read().decode())
        print("Occupancy:", data.get("occupancy_status"))
        print("Verification Status:", data.get("verification_status"))
        print("Message:", data.get("message"))
except Exception as e:
    print("Error:", e)
