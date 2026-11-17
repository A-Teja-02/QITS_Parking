/* API client + types for the AI Vision / CCTV backend (proxied at /api/parking → :8001). */

// Dev: Vite proxies /api/parking to the AI backend (:8001).
// Production: set VITE_AI_API_URL to the AI backend's address, e.g. https://ai.parking.example.com
const AI_ORIGIN = ((import.meta as any).env?.VITE_AI_API_URL || '').replace(/\/$/, '');
const BASE = `${AI_ORIGIN}/api/parking`;
const authToken = () => { try { return localStorage.getItem('auth_token') || ''; } catch { return ''; } };
const authHeaders = (): Record<string, string> => (authToken() ? { Authorization: `Bearer ${authToken()}` } : {});
/** Evidence images (/captures/...) live on the AI backend too. */
export const mediaUrl = (u?: string | null) => {
  if (!u || !u.startsWith('/')) return u || '';
  // Evidence photos are served from the database by the AI backend and need the admin login
  return u.startsWith('/api/') ? `${AI_ORIGIN}${u}${u.includes('?') ? '&' : '?'}token=${encodeURIComponent(authToken())}` : `${AI_ORIGIN}${u}`;
};

export interface SlotPolygon {
  slot_id: string;
  points: number[][]; // normalized [x, y] in 0..1
  label?: string | null;
}

export interface Camera {
  id: string;
  name: string;
  location?: string | null;
  source_type: string;
  brand?: string | null;
  rtsp_url?: string | null;
  host?: string | null;
  port?: number;
  username?: string | null;
  password?: string | null;
  stream_path?: string | null;
  channel?: number;
  transport?: string;
  stream_profile?: string;
  is_active: boolean;
  status: string;
  last_error?: string | null;
  resolution?: string | null;
  fps?: number;
  monitoring?: boolean;
  stream_source?: string;
  monitored_slots: SlotPolygon[];
  enable_plate_recognition?: boolean;
  min_confidence?: number;
  monitoring_enabled?: boolean;
  analysis_interval?: number;
  detection_imgsz?: number;
  violation_dwell_seconds?: number;
  flag_unreadable_plates?: boolean;
}

export type CameraInput = Partial<Omit<Camera, 'id' | 'status' | 'monitoring' | 'stream_source'>> & { name: string };

export interface MatchedUser {
  id?: string;
  employee_id?: string | null;
  name?: string;
  email?: string;
  role?: string;
  department?: string;
  mobile_number?: string;
  vehicle_number?: string;
  reserved_slot_today?: string | null;
  slot_is_permanent?: boolean; // reserved_slot_today is their permanently assigned slot
  reserved_vehicle_today?: string | null;
}

export type SlotVerdict =
  | 'VACANT' | 'DETECTING' | 'READING_PLATE' | 'OCCUPIED_NO_PLATE' | 'VERIFYING'
  | 'CORRECT_PARKING' | 'WRONG_SLOT' | 'UNAUTHORIZED';

export interface LiveSlot {
  slot_id: string;
  slot_name?: string;
  label?: string;
  polygon: number[][];
  occupancy_status: 'occupied' | 'vacant';
  occupancy_source?: 'detector' | 'appearance' | null;
  occupied_seconds: number;
  verification_status: SlotVerdict;
  message: string;
  detected_plate?: string | null;
  raw_plate?: string | null;
  confidence: number;
  plate_match?: 'exact' | 'fuzzy' | 'none' | null;
  plate_reads: number;
  plate_crop_base64?: string | null;
  unreadable_reason?: string | null;
  expected_user?: string | null;
  expected_plate?: string | null;
  expected_source?: 'reservation' | 'manager_assigned' | 'none' | null;
  actual_driver?: string | null;
  matched_user?: MatchedUser | null;
  violation_type?: string | null;
  violation_id?: string | null;
  has_empty_baseline?: boolean;
}

export interface LiveStatus {
  camera_id: string;
  camera_name: string;
  stream_status: string;
  stream_error?: string | null;
  fps: number;
  resolution?: string | null;
  running: boolean;
  last_cycle_ms?: number;
  last_cycle_at?: string | null;
  last_error?: string | null;
  total_slots: number;
  occupied_count: number;
  vacant_count: number;
  violation_count: number;
  slots: LiveSlot[];
}

export interface ParkingSlotInfo {
  id: string;
  label: string;
  floor_id: string;
  floor_name?: string;
  booked_by?: string | null;
  booked_vehicle?: string | null;
  booking_source?: string | null;
}

export interface Violation {
  id: string;
  camera_id: string;
  camera_name: string;
  slot_id: string;
  slot_label?: string | null;
  floor?: string | null;
  violation_type: 'WRONG_SLOT' | 'NO_BOOKING' | 'UNKNOWN_VEHICLE' | 'UNREADABLE_PLATE' | string;
  severity?: string;
  detected_plate?: string | null;
  raw_plate?: string | null;
  plate_confidence?: number | null;
  plate_match?: string | null;
  plate_reads?: number | null;
  matched_user?: MatchedUser | null;
  expected?: { source?: string; user_name?: string | null; vehicle_number?: string | null; email?: string | null } | null;
  message?: string;
  snapshot_url?: string | null;
  vehicle_crop_url?: string | null;
  plate_crop_url?: string | null;
  occupied_since?: string | null;
  vehicle_left_at?: string | null;
  created_at: string;
  status: 'open' | 'resolved' | 'false_alarm';
  notes?: string | null;
  corrected_plate?: string | null;
}

export interface OnvifDevice { ip: string; port: number; name?: string | null; hardware?: string | null; location?: string | null; xaddrs: string[] }
export interface OnvifStream { token: string; name: string; width: number; height: number; encoding?: string | null; rtsp_url: string; display_url: string }

export type DetectMethod = 'auto' | 'lines' | 'vehicles';

export interface PhotoSlotResult {
  slot_id: string;
  slot_name?: string;
  polygon: number[][];
  occupancy_status: 'occupied' | 'vacant';
  occupancy_source?: 'detector' | 'zoom' | 'plate' | null;
  verification_status: SlotVerdict;
  detected_plate?: string | null;
  raw_plate?: string | null;
  plate_match?: 'exact' | 'fuzzy' | 'none' | null;
  confidence: number;
  plate_reads?: number;
  violation_type?: string | null;
  violation_id?: string | null;
  already_logged?: boolean;
  message: string;
  vehicle_box?: number[] | null;
  vehicle_class?: string | null;
  plate_crop_base64?: string | null;
  vehicle_crop_base64?: string | null;
  unreadable_reason?: string | null;
  suggested_plate?: string | null;
  expected_user?: string | null;
  expected_plate?: string | null;
  expected_source?: string | null;
  floor?: string | null;
  matched_user?: MatchedUser | null;
}

export interface PhotoCheckResult {
  total_slots: number;
  occupied_count: number;
  vacant_count: number;
  violation_count: number;
  slots: PhotoSlotResult[];
  violations: PhotoSlotResult[];
  detections: Array<{ class_name: string; confidence: number; box: number[] }>;
  image_size: [number, number];
  elapsed_ms: number;
  recorded: boolean;
  error?: string;
}

export const VERDICT_STROKE: Record<string, string> = {
  VACANT: '#22C55E', CORRECT_PARKING: '#16A34A', WRONG_SLOT: '#F97316', UNAUTHORIZED: '#EF4444',
  OCCUPIED_NO_PLATE: '#EAB308', READING_PLATE: '#EAB308', VERIFYING: '#3B82F6', DETECTING: '#94A3B8',
};

export interface TestResult {
  connected: boolean;
  message: string;
  resolution?: string;
  fps?: number;
  latency_ms?: number;
  stream_url?: string;
  preview_base64?: string | null;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...((init?.headers as Record<string, string>) || {}) },
  });
  if (res.status === 401) throw new Error('Your session expired or you are not logged in as admin. Please log in again.');
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j);
    } catch { /* not json */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const cctvApi = {
  health: async () => {
    try {
      const r = await fetch(`${BASE}/cameras`, { headers: authHeaders(), signal: AbortSignal.timeout(4000) });
      return r.ok;
    } catch {
      return false;
    }
  },
  cameras: () => req<Camera[]>('/cameras'),
  addCamera: (c: CameraInput) => req<Camera>('/cameras', { method: 'POST', body: JSON.stringify(c) }),
  updateCamera: (id: string, c: Partial<CameraInput>) => req<Camera>(`/cameras/${id}`, { method: 'PUT', body: JSON.stringify(c) }),
  deleteCamera: (id: string) => req<{ success: boolean }>(`/cameras/${id}`, { method: 'DELETE' }),
  testCamera: (c: Partial<CameraInput>) => req<TestResult>('/cameras/test', { method: 'POST', body: JSON.stringify(c) }),
  startCamera: (id: string) => req<{ success: boolean }>(`/cameras/${id}/start`, { method: 'POST' }),
  stopCamera: (id: string) => req<{ success: boolean }>(`/cameras/${id}/stop`, { method: 'POST' }),
  latestFrame: (id: string) =>
    req<{ image_base64: string; width: number; height: number; resolution: string }>(`/cameras/${id}/latest-frame`),
  saveSlots: (id: string, slots: SlotPolygon[]) =>
    req<{ success: boolean; total_slots: number }>(`/cameras/${id}/slots`, { method: 'PUT', body: JSON.stringify({ monitored_slots: slots }) }),
  liveStatus: (id: string) => req<LiveStatus>(`/cameras/${id}/live-status`),
  captureBaseline: (id: string, clear = false) =>
    req<{ message: string }>(`/cameras/${id}/baseline${clear ? '?clear=true' : ''}`, { method: 'POST' }),
  suggestSlots: (image_base64: string, hint = 2, method: DetectMethod = 'auto', colors?: string[]) =>
    req<{ suggested_slots: SlotPolygon[]; message: string; source: string; lines: number[][] }>('/anpr/auto-suggest-slots', {
      method: 'POST', body: JSON.stringify({ image_base64, num_slots_hint: hint, method, colors }),
    }),
  photoCheck: (image_base64: string, slots: SlotPolygon[], record: boolean) =>
    req<PhotoCheckResult>('/anpr/photo-check', {
      method: 'POST', body: JSON.stringify({ image_base64, monitored_slots: slots, record_violations: record }),
    }),
  testLayout: (image_base64: string, slots: SlotPolygon[]) =>
    req<any>('/anpr/verify-multi-slots', { method: 'POST', body: JSON.stringify({ image_base64, monitored_slots: slots, camera_id: 'layout_test' }) }),
  classifyPlate: (slot_id: string, plate: string) =>
    req<Partial<PhotoSlotResult>>('/anpr/classify-plate', { method: 'POST', body: JSON.stringify({ slot_id, plate }) }),
  onvifDiscover: () => req<{ count: number; devices: OnvifDevice[] }>('/onvif/discover', { method: 'POST' }),
  onvifStreams: (host: string, port: number, username: string, password: string) =>
    req<{ device: Record<string, string | null>; clock_offset_seconds: number; streams: OnvifStream[] }>('/onvif/streams', {
      method: 'POST', body: JSON.stringify({ host, port, username, password }),
    }),
  systemStatus: () => req<{ database: string; connected: boolean; error?: string | null }>('/system/status'),
  parkingSlots: () => req<ParkingSlotInfo[]>('/slots'),
  violations: (params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') q.set(k, String(v)); });
    return req<{ total: number; items: Violation[]; status_counts: Record<string, number> }>(`/violations?${q}`);
  },
  updateViolation: (id: string, body: { status?: string; notes?: string; corrected_plate?: string }) =>
    req<Violation>(`/violations/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  violation: (id: string) => req<Violation>(`/violations/${id}`),
  deleteViolation: (id: string) => req<{ success: boolean }>(`/violations/${id}`, { method: 'DELETE' }),
};

export const snapshotUrl = (id: string, annotated = true, maxWidth = 1280) =>
  `${BASE}/cameras/${id}/snapshot?annotated=${annotated}&max_width=${maxWidth}&quality=75&token=${encodeURIComponent(authToken())}&_=${Date.now()}`;

export const streamUrl = (id: string, annotated = true, nonce = 0) =>
  `${BASE}/cameras/${id}/stream?annotated=${annotated}&_=${nonce}&token=${encodeURIComponent(authToken())}`;

/* ─── Shared UI helpers ─────────────────────────────────────── */
export const VERDICT_STYLE: Record<string, { label: string; color: string; bg: string; border: string }> = {
  VACANT: { label: 'Vacant', color: '#047857', bg: '#ECFDF5', border: '#A7F3D0' },
  DETECTING: { label: 'Vehicle arriving', color: '#475569', bg: '#F1F5F9', border: '#CBD5E1' },
  READING_PLATE: { label: 'Reading plate', color: '#B45309', bg: '#FFFBEB', border: '#FDE68A' },
  OCCUPIED_NO_PLATE: { label: 'Plate unreadable', color: '#92400E', bg: '#FEF3C7', border: '#FCD34D' },
  VERIFYING: { label: 'Verifying', color: '#1D4ED8', bg: '#EFF6FF', border: '#BFDBFE' },
  CORRECT_PARKING: { label: 'Correct', color: '#065F46', bg: '#D1FAE5', border: '#6EE7B7' },
  WRONG_SLOT: { label: 'Wrong slot', color: '#9A3412', bg: '#FFEDD5', border: '#FDBA74' },
  UNAUTHORIZED: { label: 'Unauthorized', color: '#991B1B', bg: '#FEE2E2', border: '#FCA5A5' },
};

export const VIOLATION_LABEL: Record<string, string> = {
  WRONG_SLOT: 'Wrong slot',
  NO_BOOKING: 'No booking',
  UNKNOWN_VEHICLE: 'Unknown vehicle',
  UNREADABLE_PLATE: 'Plate unreadable',
};

export const SLOT_COLORS = ['#2563EB', '#7C3AED', '#D97706', '#059669', '#DB2777', '#0891B2', '#65A30D', '#DC2626'];

export function fmtDuration(sec: number) {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ${sec % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function fmtTime(iso?: string | null) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}
