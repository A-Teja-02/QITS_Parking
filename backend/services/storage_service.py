import os
import sqlite3
import json
import base64
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "captures.db")
CAPTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "captures")

os.makedirs(CAPTURES_DIR, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS captures (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            camera_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            labels TEXT NOT NULL,
            confidences TEXT NOT NULL,
            ocr_text TEXT,
            image_path TEXT NOT NULL,
            crop_path TEXT,
            metadata TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

class StorageService:
    @staticmethod
    def save_base64_image(b64_string: str, prefix: str = "capture") -> tuple[str, str]:
        """Saves a base64 encoded image to disk and returns (rel_url, abs_path)."""
        if "," in b64_string:
            b64_string = b64_string.split(",", 1)[1]
        
        img_data = base64.b64decode(b64_string)
        file_id = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
        abs_path = os.path.join(CAPTURES_DIR, file_id)
        
        with open(abs_path, "wb") as f:
            f.write(img_data)
            
        rel_url = f"/captures/{file_id}"
        return rel_url, abs_path

    @staticmethod
    def create_capture(
        timestamp: str,
        camera_id: str,
        reason: str,
        labels: List[str],
        confidences: List[float],
        image_base64: str,
        crop_base64: Optional[str] = None,
        ocr_text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        image_url, abs_img_path = StorageService.save_base64_image(image_base64, prefix="frame")
        crop_url = None
        abs_crop_path = None
        if crop_base64:
            crop_url, abs_crop_path = StorageService.save_base64_image(crop_base64, prefix="crop")

        capture_id = f"cap_{uuid.uuid4().hex[:10]}"
        meta_json = json.dumps(metadata or {})
        labels_json = json.dumps(labels)
        conf_json = json.dumps(confidences)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO captures (id, timestamp, camera_id, reason, labels, confidences, ocr_text, image_path, crop_path, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (capture_id, timestamp, camera_id, reason, labels_json, conf_json, ocr_text, image_url, crop_url, meta_json))
        conn.commit()
        conn.close()

        return {
            "id": capture_id,
            "timestamp": timestamp,
            "camera_id": camera_id,
            "reason": reason,
            "labels": labels,
            "confidences": confidences,
            "ocr_text": ocr_text,
            "image_url": image_url,
            "crop_url": crop_url,
            "metadata": metadata or {}
        }

    @staticmethod
    def get_captures(
        label_filter: Optional[str] = None,
        reason_filter: Optional[str] = None,
        search_query: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = "SELECT * FROM captures WHERE 1=1"
        params = []

        if label_filter and label_filter.lower() != "all":
            query += " AND labels LIKE ?"
            params.append(f"%{label_filter}%")

        if reason_filter and reason_filter.lower() != "all":
            query += " AND reason = ?"
            params.append(reason_filter)

        if search_query:
            query += " AND (ocr_text LIKE ? OR labels LIKE ? OR reason LIKE ?)"
            term = f"%{search_query}%"
            params.extend([term, term, term])

        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            results.append({
                "id": row["id"],
                "timestamp": row["timestamp"],
                "camera_id": row["camera_id"],
                "reason": row["reason"],
                "labels": json.loads(row["labels"]),
                "confidences": json.loads(row["confidences"]),
                "ocr_text": row["ocr_text"],
                "image_url": row["image_path"],
                "crop_url": row["crop_path"],
                "metadata": json.loads(row["metadata"] or "{}")
            })

        conn.close()
        return results

    @staticmethod
    def update_ocr_text(capture_id: str, ocr_text: str) -> bool:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE captures SET ocr_text = ? WHERE id = ?", (ocr_text, capture_id))
        modified = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return modified

    @staticmethod
    def delete_capture(capture_id: str) -> bool:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT image_path, crop_path FROM captures WHERE id = ?", (capture_id,))
        row = cursor.fetchone()
        
        if row:
            for p in row:
                if p and p.startswith("/captures/"):
                    fname = os.path.basename(p)
                    full_p = os.path.join(CAPTURES_DIR, fname)
                    if os.path.exists(full_p):
                        try:
                            os.remove(full_p)
                        except Exception:
                            pass
            cursor.execute("DELETE FROM captures WHERE id = ?", (capture_id,))
            conn.commit()
            conn.close()
            return True
        conn.close()
        return False

    @staticmethod
    def clear_all_captures() -> int:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT image_path, crop_path FROM captures")
        rows = cursor.fetchall()
        for row in rows:
            for p in row:
                if p and p.startswith("/captures/"):
                    fname = os.path.basename(p)
                    full_p = os.path.join(CAPTURES_DIR, fname)
                    if os.path.exists(full_p):
                        try:
                            os.remove(full_p)
                        except Exception:
                            pass
        cursor.execute("DELETE FROM captures")
        count = cursor.rowcount
        conn.commit()
        conn.close()
        return count

    @staticmethod
    def get_total_count() -> int:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM captures")
        count = cursor.fetchone()[0]
        conn.close()
        return count
