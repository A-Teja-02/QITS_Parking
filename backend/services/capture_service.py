import time
from typing import Dict, List, Any, Optional
from datetime import datetime

class CaptureRuleEngine:
    def __init__(self):
        # Maps rule_id -> last_triggered_timestamp
        self._last_trigger_times: Dict[str, float] = {}
        # Maps track_id -> first_seen_time
        self._track_first_seen: Dict[int, float] = {}
        # Maps track_id -> last_capture_time
        self._track_captured_times: Dict[int, float] = {}

    def should_trigger(
        self,
        rule_id: str,
        cooldown_seconds: float = 5.0,
        track_id: Optional[int] = None
    ) -> bool:
        now = time.time()
        
        # Check rule cooldown
        last_rule_time = self._last_trigger_times.get(rule_id, 0.0)
        if now - last_rule_time < cooldown_seconds:
            return False

        # If tracking ID is present, suppress duplicates for the same object
        if track_id is not None:
            last_obj_capture = self._track_captured_times.get(track_id, 0.0)
            if now - last_obj_capture < (cooldown_seconds * 2.0):
                return False

        return True

    def mark_triggered(self, rule_id: str, track_id: Optional[int] = None):
        now = time.time()
        self._last_trigger_times[rule_id] = now
        if track_id is not None:
            self._track_captured_times[track_id] = now

    def update_dwell_time(self, active_track_ids: List[int], min_dwell_seconds: float) -> List[int]:
        """
        Updates dwell tracker for active object track IDs.
        Returns list of track IDs that have met or exceeded the dwell threshold.
        """
        now = time.time()
        qualified_tracks = []

        # Remove stale track IDs
        active_set = set(active_track_ids)
        for tid in list(self._track_first_seen.keys()):
            if tid not in active_set:
                del self._track_first_seen[tid]

        # Record first seen and check dwell
        for tid in active_track_ids:
            if tid not in self._track_first_seen:
                self._track_first_seen[tid] = now
            else:
                dwell = now - self._track_first_seen[tid]
                if dwell >= min_dwell_seconds:
                    qualified_tracks.append(tid)

        return qualified_tracks

    def reset(self):
        self._last_trigger_times.clear()
        self._track_first_seen.clear()
        self._track_captured_times.clear()
