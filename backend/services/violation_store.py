"""
Parking violations, stored in the MAIN parking database (same DATABASE_URL as the
parking server - SQLite locally, MySQL in production). Nothing is kept only on the
AI machine: records and evidence photos both live in the database, so any server
connected to it can show them.

Tables (created automatically if the database user may create tables; otherwise
run PART 7 of server/db/mysql_updates.sql):
  cctv_violations        one row per violation
  cctv_violation_images  scene / vehicle / plate JPEGs for a violation

Each violation keeps:
- where/when: camera, slot (id + name), floor, first-seen time, time the car left
- what: violation type, plate read, confidence, how the plate matched a known vehicle
- who: matched employee details and who the slot was for
- review: status (open / resolved / false_alarm), notes, manual plate correction
"""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from sqlalchemy import inspect, text

from services.identity_service import ENGINE

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY_DB = os.path.join(os.environ.get("CCTV_DATA_DIR") or os.path.join(BACKEND_DIR, "data"), "surveillance.db")
TABLE = "cctv_violations"
IMG_TABLE = "cctv_violation_images"
_IS_MYSQL = ENGINE.dialect.name == "mysql"
_BLOB = "MEDIUMBLOB" if _IS_MYSQL else "BLOB"   # MySQL BLOB is only 64 KB

# name, SQL type (portable between SQLite and MySQL)
_COLUMNS = [
    ("id", "VARCHAR(64) NOT NULL PRIMARY KEY"),
    ("camera_id", "VARCHAR(64)"),
    ("camera_name", "VARCHAR(200)"),
    ("slot_id", "VARCHAR(64)"),
    ("slot_label", "VARCHAR(100)"),
    ("floor", "VARCHAR(100)"),
    ("violation_type", "VARCHAR(40)"),      # WRONG_SLOT | NO_BOOKING | UNKNOWN_VEHICLE | UNREADABLE_PLATE
    ("severity", "VARCHAR(20)"),
    ("detected_plate", "VARCHAR(40)"),
    ("raw_plate", "VARCHAR(40)"),
    ("plate_confidence", "DOUBLE" if _IS_MYSQL else "REAL"),
    ("plate_match", "VARCHAR(20)"),
    ("plate_reads", "INTEGER"),
    ("matched_user", "TEXT"),               # JSON
    ("expected", "TEXT"),                   # JSON
    ("message", "TEXT"),
    ("snapshot_url", "VARCHAR(300)"),
    ("vehicle_crop_url", "VARCHAR(300)"),
    ("plate_crop_url", "VARCHAR(300)"),
    ("occupied_since", "VARCHAR(32)"),
    ("vehicle_left_at", "VARCHAR(32)"),
    ("created_at", "VARCHAR(32)"),          # ISO time, sorts correctly as text
    ("status", "VARCHAR(20)"),              # open | resolved | false_alarm
    ("notes", "TEXT"),
    ("corrected_plate", "VARCHAR(40)"),
    ("updated_at", "VARCHAR(32)"),
]
_NAMES = [n for n, _ in _COLUMNS]
_JSON_FIELDS = {"matched_user", "expected"}


def image_url(vid: str, kind: str) -> str:
    return f"/api/parking/violations/{vid}/image/{kind}"


class ViolationStore:
    _instance: Optional["ViolationStore"] = None

    def __init__(self):
        self._lock = threading.Lock()
        self.ready = False
        self.error: Optional[str] = None
        self._init_db()
        if self.ready:
            self._import_legacy()

    @classmethod
    def get_instance(cls) -> "ViolationStore":
        if cls._instance is None:
            cls._instance = ViolationStore()
        return cls._instance

    # ------------------------------------------------------------------
    def _init_db(self):
        try:
            insp = inspect(ENGINE)
            existing_tables = set(insp.get_table_names())
            with ENGINE.begin() as conn:
                if TABLE not in existing_tables:
                    cols = ", ".join(f"{n} {t}" for n, t in _COLUMNS)
                    conn.execute(text(f"CREATE TABLE {TABLE} ({cols})"))
                    conn.execute(text(f"CREATE INDEX ix_{TABLE}_created ON {TABLE} (created_at)"))
                    conn.execute(text(f"CREATE INDEX ix_{TABLE}_status ON {TABLE} (status)"))
                else:  # add any column a newer version introduced
                    have = {c["name"] for c in insp.get_columns(TABLE)}
                    for n, t in _COLUMNS:
                        if n not in have:
                            conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {n} {t.replace(' NOT NULL PRIMARY KEY', '')}"))
                if IMG_TABLE not in existing_tables:
                    conn.execute(text(f"CREATE TABLE {IMG_TABLE} (violation_id VARCHAR(64) NOT NULL, kind VARCHAR(20) NOT NULL, "
                                      f"content {_BLOB} NOT NULL, created_at VARCHAR(32), PRIMARY KEY (violation_id, kind))"))
            self.ready = True
        except Exception as e:
            self.error = str(e)
            print(f"[ViolationStore] cannot prepare tables in the parking database: {e}\n"
                  f"   -> run PART 7 of server/db/mysql_updates.sql (needs CREATE permission once).")

    def _import_legacy(self):
        """One-time move of violations saved by the earlier version (backend/data/surveillance.db + image files)."""
        if not os.path.exists(LEGACY_DB):
            return
        try:
            src = sqlite3.connect(LEGACY_DB)
            src.row_factory = sqlite3.Row
            rows = [dict(r) for r in src.execute("SELECT * FROM violations")]
            src.close()
        except Exception as e:
            print(f"[ViolationStore] legacy import skipped: {e}")
            return
        moved = 0
        for r in rows:
            if self.get(r["id"]):
                continue
            for key, kind in (("snapshot_url", "scene"), ("vehicle_crop_url", "vehicle"), ("plate_crop_url", "plate")):
                url = r.get(key)
                if url and url.startswith("/captures/"):
                    path = os.path.join(BACKEND_DIR, url.lstrip("/").replace("/", os.sep))
                    if os.path.exists(path):
                        with open(path, "rb") as f:
                            self._put_image(r["id"], kind, f.read())
                        r[key] = image_url(r["id"], kind)
            for f in _JSON_FIELDS:
                try:
                    r[f] = json.loads(r[f]) if r.get(f) else None
                except Exception:
                    r[f] = None
            self.create({k: r.get(k) for k in _NAMES if k in r})
            moved += 1
        try:
            os.replace(LEGACY_DB, LEGACY_DB + ".imported")
        except OSError:
            pass
        print(f"[ViolationStore] moved {moved} earlier violation(s) into the parking database")

    # ------------------------------------------------------------------
    @staticmethod
    def _row(r) -> Dict[str, Any]:
        d = dict(r._mapping)
        for f in _JSON_FIELDS:
            try:
                d[f] = json.loads(d[f]) if d.get(f) else None
            except Exception:
                d[f] = None
        return d

    def _put_image(self, vid: str, kind: str, data: bytes):
        with ENGINE.begin() as conn:
            conn.execute(text(f"DELETE FROM {IMG_TABLE} WHERE violation_id = :v AND kind = :k"), {"v": vid, "k": kind})
            conn.execute(text(f"INSERT INTO {IMG_TABLE} (violation_id, kind, content, created_at) VALUES (:v, :k, :c, :t)"),
                         {"v": vid, "k": kind, "c": data, "t": datetime.now().isoformat(timespec="seconds")})

    @classmethod
    def save_image(cls, img: Optional[np.ndarray], vid: str, kind: str, quality: int = 90) -> Optional[str]:
        """Stores an evidence JPEG in the database; returns the URL the admin pages load it from."""
        if img is None or getattr(img, "size", 0) == 0:
            return None
        try:
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                return None
            cls.get_instance()._put_image(vid, kind, buf.tobytes())
            return image_url(vid, kind)
        except Exception as e:
            print(f"[ViolationStore] failed to save {kind} image: {e}")
            return None

    def get_image(self, vid: str, kind: str) -> Optional[bytes]:
        with ENGINE.connect() as conn:
            r = conn.execute(text(f"SELECT content FROM {IMG_TABLE} WHERE violation_id = :v AND kind = :k"),
                             {"v": vid, "k": kind}).first()
        return bytes(r[0]) if r else None

    # ------------------------------------------------------------------
    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        rec = {n: None for n in _NAMES}
        rec.update({k: v for k, v in data.items() if k in rec})
        rec["id"] = rec.get("id") or f"vio_{uuid.uuid4().hex[:12]}"
        rec["created_at"] = rec.get("created_at") or now
        rec["updated_at"] = now
        rec["status"] = rec.get("status") or "open"
        row = {k: (json.dumps(v) if k in _JSON_FIELDS and v is not None else v) for k, v in rec.items()}
        with self._lock, ENGINE.begin() as conn:
            conn.execute(text(f"INSERT INTO {TABLE} ({', '.join(_NAMES)}) VALUES ({', '.join(':' + n for n in _NAMES)})"), row)
        return rec

    def update(self, vid: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        allowed = set(_NAMES) - {"id", "created_at"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if fields:
            fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
            params = {k: (json.dumps(v) if k in _JSON_FIELDS and v is not None else v) for k, v in fields.items()}
            params["_id"] = vid
            with self._lock, ENGINE.begin() as conn:
                conn.execute(text(f"UPDATE {TABLE} SET {', '.join(f'{k} = :{k}' for k in fields)} WHERE id = :_id"), params)
        return self.get(vid)

    def get(self, vid: str) -> Optional[Dict[str, Any]]:
        with ENGINE.connect() as conn:
            r = conn.execute(text(f"SELECT * FROM {TABLE} WHERE id = :id"), {"id": vid}).first()
        return self._row(r) if r else None

    def list(self, limit: int = 100, offset: int = 0, status: Optional[str] = None,
             slot_id: Optional[str] = None, camera_id: Optional[str] = None,
             plate: Optional[str] = None, date: Optional[str] = None,
             date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        where, p = [], {}
        if status:
            where.append("status = :status"); p["status"] = status
        if slot_id:
            where.append("(UPPER(slot_id) = :slot OR UPPER(slot_label) LIKE :slotlike)")
            p["slot"] = slot_id.upper(); p["slotlike"] = f"%{slot_id.upper()}%"
        if camera_id:
            where.append("camera_id = :cam"); p["cam"] = camera_id
        if plate:
            where.append("(REPLACE(UPPER(detected_plate), ' ', '') LIKE :pl OR REPLACE(UPPER(corrected_plate), ' ', '') LIKE :pl)")
            p["pl"] = f"%{plate.upper().replace(' ', '')}%"
        if date:  # single day
            date_from = date_to = date
        if date_from:
            where.append("created_at >= :dfrom"); p["dfrom"] = date_from
        if date_to:
            nxt = (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            where.append("created_at < :dto"); p["dto"] = nxt
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with ENGINE.connect() as conn:
            total = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE} {clause}"), p).scalar() or 0
            rows = conn.execute(text(f"SELECT * FROM {TABLE} {clause} ORDER BY created_at DESC LIMIT :lim OFFSET :off"),
                                {**p, "lim": limit, "off": offset}).fetchall()
            # status counts follow the same date / slot / plate filters, so the tabs match what is shown
            base = [w for w in where if not w.startswith("status")]
            bclause = ("WHERE " + " AND ".join(base)) if base else ""
            bp = {k: v for k, v in p.items() if k != "status"}
            counts = {r[0]: r[1] for r in conn.execute(text(f"SELECT status, COUNT(*) FROM {TABLE} {bclause} GROUP BY status"), bp)}
        return {"total": total, "items": [self._row(r) for r in rows], "status_counts": counts}

    def find_recent(self, camera_id: str, slot_id: str, plate: Optional[str], minutes: int = 10) -> Optional[Dict[str, Any]]:
        """Same camera + slot + plate within the last few minutes (avoids duplicates when a photo is re-checked)."""
        cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        norm = (plate or "").upper().replace(" ", "")
        with ENGINE.connect() as conn:
            rows = conn.execute(text(f"SELECT * FROM {TABLE} WHERE camera_id = :c AND UPPER(slot_id) = :s AND created_at >= :t "
                                     f"ORDER BY created_at DESC"), {"c": camera_id, "s": slot_id.upper(), "t": cutoff}).fetchall()
        for r in rows:
            d = self._row(r)
            if (d.get("detected_plate") or "").upper().replace(" ", "") == norm:
                return d
        return None

    def delete(self, vid: str) -> bool:
        if not self.get(vid):
            return False
        with self._lock, ENGINE.begin() as conn:
            conn.execute(text(f"DELETE FROM {IMG_TABLE} WHERE violation_id = :v"), {"v": vid})
            conn.execute(text(f"DELETE FROM {TABLE} WHERE id = :v"), {"v": vid})
        return True
