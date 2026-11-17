"""
Looks up "who should be in this slot" and "who owns this plate" from the main
parking database (server/parking.db).

- Expected occupant of a slot today:
    1. an active reservation for that slot today, else
    2. the manager the slot is permanently assigned to (unless they released it today), else
    3. nobody (slot is unbooked).
- Plate owner: employee whose registered vehicle, or whose reservation vehicle today,
  matches the plate. Matching is exact first, then a conservative fuzzy match
  (one OCR character off) against the closed set of known company plates.

Results are cached briefly so the camera monitors don't hit SQLite every frame.
"""
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER_DIR = os.path.join(BASE_DIR, "server")


def _read_server_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    path = os.path.join(SERVER_DIR, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def resolve_database_url() -> str:
    """
    Same database as the main parking server, so bookings, releases and slot names are
    always current:
      1. PARKING_DATABASE_URL (AI backend override)
      2. PARKING_DB_PATH (a SQLite file - used by tests)
      3. DATABASE_URL from the environment or server/.env (e.g. mysql+pymysql://user:pass@host:3306/parking_db)
      4. server/parking.db
    """
    if os.environ.get("PARKING_DATABASE_URL"):
        return os.environ["PARKING_DATABASE_URL"]
    if os.environ.get("PARKING_DB_PATH"):
        return "sqlite:///" + os.path.abspath(os.environ["PARKING_DB_PATH"]).replace("\\", "/")
    url = os.environ.get("DATABASE_URL") or _read_server_env().get("DATABASE_URL") or ""
    if not url:
        return "sqlite:///" + os.path.join(SERVER_DIR, "parking.db").replace("\\", "/")
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        rel = url[len("sqlite:///"):]
        if not os.path.isabs(rel):  # relative to the server folder, like the main server sees it
            url = "sqlite:///" + os.path.abspath(os.path.join(SERVER_DIR, rel)).replace("\\", "/")
    return url


DATABASE_URL = resolve_database_url()
_kwargs: Dict[str, Any] = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False, "timeout": 5}
else:
    _kwargs.update(pool_size=3, max_overflow=5, pool_recycle=280)
    if DATABASE_URL.startswith("mysql"):
        _kwargs["connect_args"] = {"charset": "utf8mb4", "connect_timeout": 5}
ENGINE = create_engine(DATABASE_URL, **_kwargs)
Q = ENGINE.dialect.identifier_preparer.quote  # quotes reserved words per database ("order" vs `order`)


def db_label() -> str:
    """Database type and host for logs - never the password."""
    try:
        u = ENGINE.url
        return f"{u.get_backend_name()}://{u.host or ''}{'/' + u.database if u.database else ''}"
    except Exception:
        return "unknown"


def db_execute(sql: str, params: Optional[Dict[str, Any]] = None) -> bool:
    try:
        with ENGINE.begin() as conn:
            conn.execute(text(sql), params or {})
        return True
    except Exception as e:
        print(f"[IdentityService] write failed: {e}")
        return False

# Characters OCR commonly confuses on number plates. A substitution between two
# characters of the same group counts as a "cheap" edit for fuzzy matching.
_CONFUSABLE = [set("0OQD"), set("1IL7"), set("2Z"), set("5S"), set("8B"), set("6G"), set("4A"), set("UV")]


def normalize_plate(plate: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", (plate or "").upper())


def _sub_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    for grp in _CONFUSABLE:
        if a in grp and b in grp:
            return 0.5
    return 1.0


def plate_distance(a: str, b: str) -> float:
    """Weighted edit distance where look-alike characters (0/O, 8/B, ...) cost 0.5."""
    a, b = normalize_plate(a), normalize_plate(b)
    if a == b:
        return 0.0
    la, lb = len(a), len(b)
    prev = [float(j) for j in range(lb + 1)]
    for i in range(1, la + 1):
        cur = [float(i)] + [0.0] * lb
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1.0, cur[j - 1] + 1.0, prev[j - 1] + _sub_cost(a[i - 1], b[j - 1]))
        prev = cur
    return prev[lb]


class IdentityService:
    _instance: Optional["IdentityService"] = None

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: Dict[str, Tuple[float, Any]] = {}
        # Short cache: a new booking / release / slot rename is picked up within seconds
        self.cache_ttl = float(os.environ.get("IDENTITY_CACHE_SECONDS", "8"))
        self.last_error: Optional[str] = None

    @classmethod
    def get_instance(cls) -> "IdentityService":
        if cls._instance is None:
            cls._instance = IdentityService()
        return cls._instance

    # ------------------------------------------------------------------
    def _cached(self, key: str, loader):
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.cache_ttl:
                return hit[1]
        value = loader()
        with self._lock:
            self._cache[key] = (now, value)
        return value

    def invalidate(self):
        with self._lock:
            self._cache.clear()

    def _query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        try:
            with ENGINE.connect() as conn:
                rows = [dict(r._mapping) for r in conn.execute(text(sql), params or {})]
            self.last_error = None
            return rows
        except Exception as e:
            self.last_error = str(e)
            print(f"[IdentityService] query failed ({db_label()}): {e}")
            return []

    def status(self) -> Dict[str, Any]:
        ok = bool(self._query("SELECT 1 AS ok"))
        return {"database": db_label(), "connected": ok, "error": None if ok else self.last_error}

    # ------------------------------------------------------------------
    def employees(self) -> List[Dict[str, Any]]:
        return self._cached("employees", lambda: self._query(
            "SELECT id, employee_id, name, email, role, department, vehicle_number, mobile_number, is_active "
            "FROM employees"))

    def employee_by_id(self, emp_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not emp_id:
            return None
        return next((e for e in self.employees() if e["id"] == emp_id), None)

    def reservations_for(self, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        return self._cached(f"res_{date_str}", lambda: self._query(
            "SELECT id, user_id, user_name, slot_id, vehicle_number, date, status FROM reservations "
            "WHERE date = :d AND status = 'active'", {"d": date_str}))

    def slots(self) -> List[Dict[str, Any]]:
        return self._cached("slots", lambda: self._query(
            "SELECT s.id, s.label, s.floor_id, s.position, s.status, s.reserved_for_manager_id, "
            "f.name AS floor_name FROM parking_slots s LEFT JOIN floors f ON f.id = s.floor_id "
            f"ORDER BY f.{Q('order')}, s.position"))

    def releases_for(self, date_str: str) -> List[Dict[str, Any]]:
        return self._cached(f"rel_{date_str}", lambda: self._query(
            "SELECT manager_id, slot_id, release_date FROM manager_releases WHERE release_date = :d", {"d": date_str}))

    def slot_name(self, slot_id: Optional[str]) -> Optional[str]:
        """The name the admin gave a slot (e.g. 106A); falls back to its id."""
        if not slot_id:
            return slot_id
        s = next((x for x in self.slots() if (x["id"] or "").upper() == slot_id.upper()), None)
        return (s["label"] or slot_id) if s else slot_id

    # ------------------------------------------------------------------
    def slot_expectation(self, slot_id: str, date_str: Optional[str] = None) -> Dict[str, Any]:
        """Who is allowed to park in slot_id today, and with which vehicle(s)."""
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        sid = (slot_id or "").upper()
        slot = next((s for s in self.slots() if (s["id"] or "").upper() == sid), None)
        if slot is None:  # layout may reference the slot by its name; map it to the real slot id
            slot = next((s for s in self.slots() if (s["label"] or "").strip().upper() == sid), None)
            if slot:
                sid = slot["id"].upper()
        res = next((r for r in self.reservations_for(date_str) if (r["slot_id"] or "").upper() == sid), None)

        if res:
            emp = self.employee_by_id(res["user_id"])
            plates = [p for p in {res.get("vehicle_number"), emp.get("vehicle_number") if emp else None} if p]
            return {
                "slot_id": slot_id, "source": "reservation", "user_id": res["user_id"],
                "user_name": res["user_name"], "vehicle_number": res["vehicle_number"],
                "allowed_plates": plates, "employee": emp,
                "floor": slot["floor_name"] if slot else None, "slot_label": slot["label"] if slot else None,
            }

        if slot and slot.get("reserved_for_manager_id"):
            released = any(r["slot_id"] == slot["id"] for r in self.releases_for(date_str))
            if not released:
                mgr = self.employee_by_id(slot["reserved_for_manager_id"])
                if mgr:
                    return {
                        "slot_id": slot_id, "source": "manager_assigned", "user_id": mgr["id"],
                        "user_name": mgr["name"], "vehicle_number": mgr.get("vehicle_number") or "",
                        "allowed_plates": [mgr["vehicle_number"]] if mgr.get("vehicle_number") else [],
                        "employee": mgr, "floor": slot["floor_name"], "slot_label": slot["label"],
                    }

        return {
            "slot_id": slot_id, "source": "none", "user_id": None, "user_name": None,
            "vehicle_number": None, "allowed_plates": [], "employee": None,
            "floor": slot["floor_name"] if slot else None, "slot_label": slot["label"] if slot else None,
        }

    # ------------------------------------------------------------------
    def known_plates(self, date_str: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """normalized plate -> {employee, reservation} for every company plate we know."""
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")

        def load():
            out: Dict[str, Dict[str, Any]] = {}
            emps = {e["id"]: e for e in self.employees()}
            for e in emps.values():
                n = normalize_plate(e.get("vehicle_number"))
                if len(n) >= 6:
                    out.setdefault(n, {"employee": e, "reservation": None, "plate": e["vehicle_number"]})
            for r in self.reservations_for(date_str):
                n = normalize_plate(r.get("vehicle_number"))
                if len(n) < 6:
                    continue
                entry = out.setdefault(n, {"employee": emps.get(r["user_id"]), "reservation": None,
                                           "plate": r["vehicle_number"]})
                entry["reservation"] = r
                if entry["employee"] is None:
                    entry["employee"] = emps.get(r["user_id"])
            # Employees whose registered car differs from the one on today's reservation
            for r in self.reservations_for(date_str):
                e = emps.get(r["user_id"])
                if e:
                    n = normalize_plate(e.get("vehicle_number"))
                    if n in out and out[n]["reservation"] is None:
                        out[n]["reservation"] = r
            return out

        return self._cached(f"known_{date_str}", load)

    def match_plate(self, plate: str, date_str: Optional[str] = None) -> Dict[str, Any]:
        """
        Finds who a plate belongs to.
        Returns {match: 'exact'|'fuzzy'|'none', plate (canonical), employee, reservation, distance}.
        Fuzzy match only when exactly one known plate is within distance 1.0 and the
        runner-up is clearly further away, so a stranger's car is not mistaken for an employee's.
        """
        n = normalize_plate(plate)
        known = self.known_plates(date_str)
        if not n:
            return {"match": "none", "plate": plate, "employee": None, "reservation": None, "distance": None}
        if n in known:
            k = known[n]
            return {"match": "exact", "plate": k["plate"], "employee": k["employee"],
                    "reservation": k["reservation"] or self.assigned_slot_for(k["employee"], date_str), "distance": 0.0}
        scored = sorted(((plate_distance(n, kn), kn) for kn in known), key=lambda t: t[0])
        if scored and scored[0][0] <= 1.0 and (len(scored) == 1 or scored[1][0] - scored[0][0] >= 1.0):
            k = known[scored[0][1]]
            return {"match": "fuzzy", "plate": k["plate"], "employee": k["employee"],
                    "reservation": k["reservation"] or self.assigned_slot_for(k["employee"], date_str),
                    "distance": scored[0][0]}
        return {"match": "none", "plate": plate, "employee": None, "reservation": None,
                "distance": scored[0][0] if scored else None}

    def assigned_slot_for(self, emp: Optional[Dict[str, Any]], date_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        A slot permanently assigned to this person in Admin -> Parking Lots counts as their
        booking every day - unless they released it for that day. Returned in the same shape
        as a reservation (plus source='manager_assigned') so all checks treat it the same way.
        """
        if not emp or not emp.get("id"):
            return None
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        slot = next((x for x in self.slots() if x.get("reserved_for_manager_id") == emp["id"]), None)
        if not slot:
            return None
        if any(r["slot_id"] == slot["id"] for r in self.releases_for(date_str)):
            return None  # released today: the slot is open to others, the holder needs a booking
        return {"id": None, "user_id": emp["id"], "user_name": emp.get("name"), "slot_id": slot["id"],
                "vehicle_number": emp.get("vehicle_number"), "date": date_str, "status": "active",
                "source": "manager_assigned"}

    def hr_user_ids(self) -> List[str]:
        return [e["id"] for e in self.employees() if (e.get("role") or "").lower() == "hr" and e.get("is_active", 1)]

    def hr_emails(self) -> List[str]:
        return [e["email"] for e in self.employees()
                if (e.get("role") or "").lower() == "hr" and e.get("is_active", 1) and e.get("email")]
