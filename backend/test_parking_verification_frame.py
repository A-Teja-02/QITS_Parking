import urllib.request
import json
import cv2
import numpy as np
import base64

base_url = "http://localhost:8001"

# Generate real 640x480 frame
frame = np.zeros((480, 640, 3), dtype=np.uint8)
frame[:] = (40, 45, 55) # dark background

# Draw a simulated car body
cv2.rectangle(frame, (100, 120), (540, 420), (160, 60, 40), -1)

# Draw a white license plate
cv2.rectangle(frame, (220, 320), (420, 380), (255, 255, 255), -1)
cv2.rectangle(frame, (220, 320), (420, 380), (0, 0, 0), 2)
# Blue IND strip
cv2.rectangle(frame, (220, 320), (250, 380), (180, 50, 20), -1)

# Text: MH 12 CD 1234 (Amit Kumar, reserved for P103)
cv2.putText(frame, "MH 12 CD 1234", (260, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)

_, buf = cv2.imencode('.jpg', frame)
img_b64 = "data:image/jpeg;base64," + base64.b64encode(buf).decode('utf-8')

print("\n--- Testing Slot Verification: Car MH 12 CD 1234 placed in P101 (Wrong Slot!) ---")
payload = {
    "image_base64": img_b64,
    "slot_id": "P101",
    "camera_id": "test_cam"
}
req = urllib.request.Request(
    f"{base_url}/api/parking/anpr/verify-slot",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req) as res:
        print("Status:", res.getcode())
        data = json.loads(res.read().decode())
        print("Occupancy:", data.get("occupancy_status"))
        print("Verification Status:", data.get("verification_status"))
        print("Detected Plate:", data.get("detected_plate"))
        print("Expected Plate:", data.get("expected_plate"))
        print("Actual Driver:", data.get("actual_driver"))
        print("Message:", data.get("message", "").encode("ascii", "replace").decode("ascii"))
        print("Alert Sent:", data.get("alert_result", {}).get("alert_sent") if data.get("alert_result") else None)
        print("Snapshot Path:", data.get("alert_result", {}).get("snapshot_path") if data.get("alert_result") else None)
        print("Email Dispatched:", data.get("alert_result", {}).get("email_dispatched") if data.get("alert_result") else None)
except Exception as e:
    print("Error:", e)
