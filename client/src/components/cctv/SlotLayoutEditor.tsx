import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Camera as CameraIcon, Upload, RefreshCw, Save, Trash2, Copy, MousePointer2, PenTool, Rows3,
  Wand2, ShieldCheck, AlertTriangle, CheckCircle, Undo2, X, Info, Maximize, Eye, EyeOff, ScanSearch,
} from 'lucide-react';
import {
  cctvApi, SLOT_COLORS, VERDICT_STROKE, type Camera, type DetectMethod, type ParkingSlotInfo,
  type PhotoCheckResult, type SlotPolygon,
} from './cctvApi';
import { PhotoResults } from './PhotoResults';

type Tool = 'select' | 'slot' | 'row';
type Pt = [number, number];
type Mode = 'camera' | 'photo';

const DETECT_OPTIONS: { id: string; label: string; method: DetectMethod; colors?: string[] }[] = [
  { id: 'auto', label: 'Auto (painted lines, then cars)', method: 'auto', colors: ['yellow', 'blue'] },
  { id: 'yellow', label: 'Yellow floor lines', method: 'lines', colors: ['yellow'] },
  { id: 'blue', label: 'Blue floor lines', method: 'lines', colors: ['blue'] },
  { id: 'white', label: 'White floor lines', method: 'lines', colors: ['white'] },
  { id: 'all', label: 'Any colour lines', method: 'lines', colors: ['yellow', 'blue', 'white'] },
  { id: 'cars', label: 'Around parked cars', method: 'vehicles' },
];
const PHOTO_SLOTS_KEY = 'cctv.photoTest.slots';

/* Perspective mapping of the unit square onto a quad (TL, TR, BR, BL). */
function squareToQuad(q: number[][]) {
  const [[x0, y0], [x1, y1], [x2, y2], [x3, y3]] = q;
  const dx1 = x1 - x2, dx2 = x3 - x2, dy1 = y1 - y2, dy2 = y3 - y2;
  const sx = x0 - x1 + x2 - x3, sy = y0 - y1 + y2 - y3;
  let g = 0, h = 0;
  if (Math.abs(sx) > 1e-9 || Math.abs(sy) > 1e-9) {
    const den = dx1 * dy2 - dx2 * dy1 || 1e-9;
    g = (sx * dy2 - dx2 * sy) / den;
    h = (dx1 * sy - sx * dy1) / den;
  }
  const a = x1 - x0 + g * x1, b = x3 - x0 + h * x3, d = y1 - y0 + g * y1, e = y3 - y0 + h * y3;
  return (u: number, v: number): Pt => {
    const w = g * u + h * v + 1;
    return [(a * u + b * v + x0) / w, (d * u + e * v + y0) / w];
  };
}

const r3 = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 1000) / 1000;

function nextIds(start: string, count: number, all: ParkingSlotInfo[], taken: Set<string>): string[] {
  const ids: string[] = [];
  const order = all.map(s => s.id.toUpperCase());
  const idx = order.indexOf(start.toUpperCase());
  if (idx >= 0) {
    for (let i = idx; i < order.length && ids.length < count; i++) if (!taken.has(order[i])) ids.push(order[i]);
  }
  // Slots exist only if created in Admin -> Parking Lots: never invent IDs. May return fewer than `count`.
  if (idx < 0) for (const id of order) { if (ids.length >= count) break; if (!taken.has(id) && !ids.includes(id)) ids.push(id); }
  return ids;
}

/** Downscale big phone photos before upload (faster, same accuracy). */
function loadImageFile(file: File, maxDim = 2200): Promise<{ src: string; w: number; h: number }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Could not read the file'));
    reader.onload = ev => {
      const img = new Image();
      img.onerror = () => reject(new Error('Not an image'));
      img.onload = () => {
        const s = Math.min(1, maxDim / Math.max(img.naturalWidth, img.naturalHeight));
        if (s >= 1) { resolve({ src: ev.target?.result as string, w: img.naturalWidth, h: img.naturalHeight }); return; }
        const c = document.createElement('canvas');
        c.width = Math.round(img.naturalWidth * s);
        c.height = Math.round(img.naturalHeight * s);
        c.getContext('2d')!.drawImage(img, 0, 0, c.width, c.height);
        resolve({ src: c.toDataURL('image/jpeg', 0.92), w: c.width, h: c.height });
      };
      img.src = ev.target?.result as string;
    };
    reader.readAsDataURL(file);
  });
}

export function SlotLayoutEditor({ cameras, initialCameraId, onSaved, mode = 'camera', onOpenViolation }: {
  cameras: Camera[];
  initialCameraId?: string | null;
  onSaved: () => void;
  mode?: Mode;
  onOpenViolation?: (id: string) => void;
}) {
  const isPhoto = mode === 'photo';
  const [camId, setCamId] = useState<string>(isPhoto ? '' : initialCameraId || cameras[0]?.id || '');
  const [frame, setFrame] = useState<string | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 1280, h: 720 });
  const [slots, setSlots] = useState<SlotPolygon[]>(() => {
    if (!isPhoto) return [];
    try { return JSON.parse(localStorage.getItem(PHOTO_SLOTS_KEY) || '[]'); } catch { return []; }
  });
  const [history, setHistory] = useState<SlotPolygon[][]>([]);
  const [dirty, setDirty] = useState(false);
  const [sel, setSel] = useState<number | null>(null);
  const [tool, setTool] = useState<Tool>('select');
  const [draft, setDraft] = useState<Pt[]>([]);
  const [rowCount, setRowCount] = useState(4);
  const [rowStartId, setRowStartId] = useState('');
  const [detectOpt, setDetectOpt] = useState('auto');
  const [lines, setLines] = useState<number[][]>([]);
  const [showLines, setShowLines] = useState(true);
  const [parkingSlots, setParkingSlots] = useState<ParkingSlotInfo[]>([]);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err' | 'info'; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<PhotoCheckResult | null>(null);
  const [record, setRecord] = useState(true);
  const [elapsed, setElapsed] = useState(0);
  const [hover, setHover] = useState<Pt | null>(null);
  const [displayW, setDisplayW] = useState(800);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const shootRef = useRef<HTMLInputElement | null>(null);
  const resultsRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ kind: 'vertex' | 'poly'; si: number; pi?: number; start: Pt; orig: number[][] } | null>(null);

  const cam = cameras.find(c => c.id === camId);
  const { w: W, h: H } = size;
  const k = W / Math.max(1, displayW); // viewBox units per screen pixel

  /* ─── data loading ─────────────────────────────────────── */
  useEffect(() => { cctvApi.parkingSlots().then(setParkingSlots).catch(() => setParkingSlots([])); }, []);
  useEffect(() => { if (!isPhoto && initialCameraId) setCamId(initialCameraId); }, [initialCameraId, isPhoto]);
  useEffect(() => { if (!isPhoto && !camId && cameras[0]) setCamId(cameras[0].id); }, [cameras, camId, isPhoto]);
  useEffect(() => { if (isPhoto) { try { localStorage.setItem(PHOTO_SLOTS_KEY, JSON.stringify(slots)); } catch { /* ignore */ } } }, [slots, isPhoto]);

  const loadFrame = useCallback(async (id = camId) => {
    if (!id) return;
    setBusy('frame');
    try {
      const f = await cctvApi.latestFrame(id);
      setFrame(f.image_base64);
      setSize({ w: f.width, h: f.height });
      setResult(null); setLines([]); setMsg(null);
    } catch (e: any) {
      setMsg({ kind: 'err', text: `No live frame: ${e.message}. Start the camera, or upload a screenshot from it.` });
    }
    setBusy(null);
  }, [camId]);

  useEffect(() => {
    if (isPhoto) return;
    const c = cameras.find(x => x.id === camId);
    setSlots(c?.monitored_slots ? JSON.parse(JSON.stringify(c.monitored_slots)) : []);
    setHistory([]); setDirty(false); setSel(null); setDraft([]); setResult(null); setLines([]);
    setFrame(null);
    if (camId) loadFrame(camId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camId]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setDisplayW(el.clientWidth || 800));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (busy !== 'check') return;
    const t0 = Date.now();
    const t = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 500);
    return () => clearInterval(t);
  }, [busy]);

  /* ─── editing helpers ──────────────────────────────────── */
  const commit = (next: SlotPolygon[]) => {
    setHistory(h => [...h.slice(-30), slots]);
    setSlots(next);
    setDirty(true);
    setResult(null);
  };
  const undo = () => {
    if (!history.length) return;
    setSlots(history[history.length - 1]);
    setHistory(history.slice(0, -1));
    setDirty(true);
  };
  const taken = useMemo(() => new Set(slots.map(s => s.slot_id.toUpperCase())), [slots]);
  const nameOf = (id: string) => parkingSlots.find(p => p.id.toUpperCase() === (id || '').toUpperCase())?.label || id;
  const slotOptionText = (p: ParkingSlotInfo) => `${p.label} · ${p.floor_name || p.floor_id}${p.booked_by ? ` · ${p.booking_source === 'manager_assigned' ? 'reserved' : 'booked'}: ${p.booked_by}` : ' · free today'}`;
  const suggestId = (extraTaken: Set<string> = new Set()) => {
    const t = new Set([...taken, ...extraTaken]);
    const free = parkingSlots.find(p => !t.has(p.id.toUpperCase()));
    return free ? free.id : '';
  };
  const NO_FREE_SLOT = 'All parking slots are already in this layout. Create more slots in Admin → Parking Lots, then add them here.';

  const toSvg = (e: { clientX: number; clientY: number }): Pt => {
    const svg = svgRef.current!;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM()!.inverse());
    return [r3(p.x / W), r3(p.y / H)];
  };

  const finishSlot = (pts: Pt[]) => {
    const id = rowStartId.trim() && !taken.has(rowStartId.trim()) ? rowStartId.trim() : suggestId();
    if (!id) { setMsg({ kind: 'err', text: NO_FREE_SLOT }); setDraft([]); return; }
    const next = [...slots, { slot_id: id, points: pts.map(p => [p[0], p[1]]), label: id }];
    commit(next);
    setSel(next.length - 1);
    setDraft([]);
    if (rowStartId) setRowStartId('');
  };

  const finishRow = (quad: Pt[]) => {
    const map = squareToQuad(quad);
    const start = rowStartId.trim() || suggestId();
    const ids = start ? nextIds(start, Math.max(1, Math.min(30, rowCount)), parkingSlots, taken) : [];
    if (!ids.length) { setMsg({ kind: 'err', text: NO_FREE_SLOT }); setDraft([]); return; }
    const n = ids.length; // fewer than asked when Parking Lots has no more free slots
    const newSlots: SlotPolygon[] = [];
    for (let i = 0; i < n; i++) {
      const u0 = i / n, u1 = (i + 1) / n;
      const pts = [map(u0, 0), map(u1, 0), map(u1, 1), map(u0, 1)].map(p => [r3(p[0]), r3(p[1])]);
      newSlots.push({ slot_id: ids[i], points: pts, label: ids[i] });
    }
    commit([...slots, ...newSlots]);
    setSel(slots.length);
    setDraft([]);
    setTool('select');
    setMsg({ kind: n < rowCount ? 'info' : 'ok', text: `Added ${n} slots (${nameOf(ids[0])} – ${nameOf(ids[n - 1])}).` +
      (n < rowCount ? ` Only ${n} free parking slot(s) were left - create more in Admin → Parking Lots for the rest.` : ' Drag any corner to fine-tune, and check the slot names on the right.') });
  };

  /* ─── pointer handling ─────────────────────────────────── */
  const onSvgDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!frame) return;
    const p = toSvg(e);
    if (tool === 'slot' || tool === 'row') {
      if (draft.length >= 3 && tool === 'slot') {
        const [fx, fy] = draft[0];
        if (Math.hypot((fx - p[0]) * W, (fy - p[1]) * H) < 14 * k) { finishSlot(draft); return; }
      }
      const nd = [...draft, p];
      if (nd.length === 4) {
        if (tool === 'slot') finishSlot(nd); else finishRow(nd);
      } else setDraft(nd);
      return;
    }
    setSel(null);
  };

  const startVertexDrag = (e: React.PointerEvent, si: number, pi: number) => {
    if (tool !== 'select') return;
    e.stopPropagation();
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setSel(si);
    drag.current = { kind: 'vertex', si, pi, start: toSvg(e), orig: slots[si].points.map(p => [...p]) };
    setHistory(h => [...h.slice(-30), slots]);
  };
  const startPolyDrag = (e: React.PointerEvent, si: number) => {
    if (tool !== 'select') return;
    e.stopPropagation();
    setSel(si);
    drag.current = { kind: 'poly', si, start: toSvg(e), orig: slots[si].points.map(p => [...p]) };
    setHistory(h => [...h.slice(-30), slots]);
  };
  const onSvgMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const p = toSvg(e);
    if (tool !== 'select') { setHover(p); return; }
    const d = drag.current;
    if (!d) return;
    const dx = p[0] - d.start[0], dy = p[1] - d.start[1];
    setSlots(prev => prev.map((s, i) => {
      if (i !== d.si) return s;
      if (d.kind === 'vertex') return { ...s, points: d.orig.map((q, j) => (j === d.pi ? [r3(q[0] + dx), r3(q[1] + dy)] : q)) };
      return { ...s, points: d.orig.map(q => [r3(q[0] + dx), r3(q[1] + dy)]) };
    }));
    setDirty(true);
    setResult(null);
  };
  const onSvgUp = () => { drag.current = null; };

  /* keyboard: delete / esc / nudge / undo */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      if (e.key === 'Escape') { setDraft([]); setTool('select'); }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); }
      if (sel === null) return;
      if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); commit(slots.filter((_, i) => i !== sel)); setSel(null); }
      const step = e.shiftKey ? 0.01 : 0.002;
      const mv: Record<string, Pt> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
      if (mv[e.key]) {
        e.preventDefault();
        const [dx, dy] = mv[e.key];
        commit(slots.map((s, i) => (i === sel ? { ...s, points: s.points.map(q => [r3(q[0] + dx), r3(q[1] + dy)]) } : s)));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  /* ─── actions ──────────────────────────────────────────── */
  const onUpload = async (file: File) => {
    try {
      const img = await loadImageFile(file);
      setSize({ w: img.w, h: img.h });
      setFrame(img.src);
      setResult(null); setLines([]);
      setMsg(isPhoto ? { kind: 'info', text: slots.length ? 'Photo loaded – check the slot outlines still fit, then press Check parking.' : 'Photo loaded – now press Detect slots (or draw them), then Check parking.' } : null);
    } catch (e: any) { setMsg({ kind: 'err', text: e.message }); }
  };

  const detectSlots = async () => {
    if (!frame) return;
    const opt = DETECT_OPTIONS.find(o => o.id === detectOpt) ?? DETECT_OPTIONS[0];
    setBusy('suggest');
    try {
      const r = await cctvApi.suggestSlots(frame, Math.max(2, slots.length), opt.method, opt.colors);
      setLines(r.lines || []);
      setShowLines(true);
      if (!r.suggested_slots.length) {
        setMsg({ kind: 'err', text: r.message });
      } else {
        const replace = !slots.length || window.confirm(`Replace the current ${slots.length} slot(s) with the ${r.suggested_slots.length} detected slots? (Cancel = add them)`);
        const base = replace ? new Set<string>() : taken;
        const ids = rowStartId.trim()
          ? nextIds(rowStartId.trim(), r.suggested_slots.length, parkingSlots, base)
          : nextIds(parkingSlots[0]?.id || '', r.suggested_slots.length, parkingSlots, base);
        if (!ids.length) { setMsg({ kind: 'err', text: NO_FREE_SLOT }); setBusy(null); return; }
        const sug = r.suggested_slots.slice(0, ids.length).map((s, i) => ({ ...s, slot_id: ids[i], label: ids[i] }));
        commit(replace ? sug : [...slots, ...sug]);
        setSel(null);
        const dropped = r.suggested_slots.length - sug.length;
        setMsg(dropped > 0
          ? { kind: 'info', text: `${r.message} ${dropped} detected slot(s) were left out - not enough free parking slots. Create them in Admin → Parking Lots.` }
          : { kind: 'ok', text: r.message });
      }
    } catch (e: any) { setMsg({ kind: 'err', text: e.message }); }
    setBusy(null);
  };

  const issues = useMemo(() => {
    const out: string[] = [];
    const seen = new Map<string, number>();
    slots.forEach(s => seen.set(s.slot_id.toUpperCase(), (seen.get(s.slot_id.toUpperCase()) || 0) + 1));
    seen.forEach((n, id) => { if (n > 1) out.push(`${nameOf(id)} is used ${n} times`); });
    const known = new Set(parkingSlots.map(p => p.id.toUpperCase()));
    if (parkingSlots.length) {
      const unknown = slots.filter(s => !known.has(s.slot_id.toUpperCase())).map(s => s.slot_id || '(empty)');
      if (unknown.length) out.push(`Pick a parking slot for: ${unknown.join(', ')} (choose its name from the list so bookings can be checked)`);
    }
    if (slots.some(s => !s.slot_id.trim())) out.push('A slot has no ID');
    return out;
  }, [slots, parkingSlots]);

  const save = async () => {
    if (!camId) return;
    if (slots.some(s => !s.slot_id.trim()) || issues.some(i => i.includes('used'))) {
      setMsg({ kind: 'err', text: 'Fix the slot IDs first (each slot needs a unique ID).' });
      return;
    }
    setBusy('save');
    try {
      const r = await cctvApi.saveSlots(camId, slots.map(s => ({ ...s, slot_id: s.slot_id.trim().toUpperCase(), label: s.slot_id.trim().toUpperCase() })));
      setDirty(false);
      setMsg({ kind: 'ok', text: `Saved ${r.total_slots} slots. The camera now monitors only these slots – see Live Monitor.` });
      onSaved();
    } catch (e: any) { setMsg({ kind: 'err', text: e.message }); }
    setBusy(null);
  };

  const check = async () => {
    if (!frame || !slots.length) return;
    if (issues.some(i => i.includes('used'))) { setMsg({ kind: 'err', text: 'Each slot needs a unique ID before checking.' }); return; }
    setBusy('check'); setElapsed(0); setMsg(null);
    try {
      const r = await cctvApi.photoCheck(frame, slots, isPhoto && record);
      setResult(r);
      setTool('select');
      setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100);
    } catch (e: any) { setMsg({ kind: 'err', text: `Check failed: ${e.message}` }); }
    setBusy(null);
  };

  const wholePhoto = () => {
    const id = rowStartId.trim() || slots[0]?.slot_id || suggestId() || parkingSlots[0]?.id || '';
    if (!id) { setMsg({ kind: 'err', text: 'No parking slots exist yet. Create them in Admin → Parking Lots first.' }); return; }
    commit([{ slot_id: id, label: id, points: [[0.01, 0.01], [0.99, 0.01], [0.99, 0.99], [0.01, 0.99]] }]);
    setSel(0);
    setMsg({ kind: 'info', text: `The whole photo is now slot ${nameOf(id)}. Pick the slot on the right to the slot this car is parked in, then press Check parking.` });
  };

  const updateId = (i: number, id: string) => {
    setSlots(prev => prev.map((s, j) => (j === i ? { ...s, slot_id: id.toUpperCase(), label: id.toUpperCase() } : s)));
    setDirty(true);
    setResult(null);
  };

  /* ─── render ───────────────────────────────────────────── */
  const handleR = 7 * k;
  const bySlot: Record<string, any> = {};
  result?.slots?.forEach(s => { bySlot[s.slot_id.toUpperCase()] = s; });

  const toolBtn = (t: Tool, icon: React.ReactNode, label: string, title: string) => (
    <button className="cctv-btn small secondary" title={title}
      style={tool === t ? { background: '#1E3A5F', color: '#fff', borderColor: '#1E3A5F' } : undefined}
      onClick={() => { setTool(t); setDraft([]); }}>{icon} {label}</button>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Source bar */}
      <div className="cctv-verify-card" style={{ padding: '12px 16px', flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 10 }}>
        {isPhoto ? (
          <>
            <button className="cctv-btn primary small" onClick={() => fileRef.current?.click()}><Upload size={14} /> Upload photo</button>
            <button className="cctv-btn secondary small" onClick={() => shootRef.current?.click()}><CameraIcon size={14} /> Take photo</button>
            <input ref={shootRef} type="file" accept="image/*" capture="environment" hidden onChange={e => { if (e.target.files?.[0]) onUpload(e.target.files[0]); e.target.value = ''; }} />
            <button className="cctv-btn secondary small" onClick={wholePhoto} disabled={!frame} title="For a close-up photo of one car"><Maximize size={14} /> Whole photo = 1 slot</button>
            {cameras.some(c => c.monitored_slots?.length) && (
              <select className="cctv-input" style={{ padding: '6px 10px', maxWidth: 220 }} value=""
                onChange={e => {
                  const c = cameras.find(x => x.id === e.target.value);
                  if (c) { commit(JSON.parse(JSON.stringify(c.monitored_slots))); setMsg({ kind: 'info', text: `Copied ${c.monitored_slots.length} slots from ${c.name}.` }); }
                }}>
                <option value="">Copy layout from camera…</option>
                {cameras.filter(c => c.monitored_slots?.length).map(c => <option key={c.id} value={c.id}>{c.name} ({c.monitored_slots.length})</option>)}
              </select>
            )}
            <span style={{ flex: 1 }} />
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#344054', cursor: 'pointer' }} title="Saved violations appear in the Violations tab with photos, and HR is notified">
              <input type="checkbox" checked={record} onChange={e => setRecord(e.target.checked)} /> Save violations to log
            </label>
            <button className="cctv-btn primary" onClick={check} disabled={!frame || !slots.length || busy === 'check'}
              style={{ background: '#DC2626', borderColor: '#DC2626', padding: '9px 18px', fontSize: 14 }}>
              {busy === 'check' ? <RefreshCw size={16} className="spin" /> : <ShieldCheck size={16} />}
              {busy === 'check' ? `Checking… ${elapsed}s` : 'Check parking'}
            </button>
          </>
        ) : (
          <>
            <label style={{ fontSize: 13, fontWeight: 600, color: '#344054' }}>Camera</label>
            <select className="cctv-input" style={{ minWidth: 220, padding: '6px 10px' }} value={camId}
              onChange={e => {
                if (dirty && !window.confirm('Discard unsaved layout changes?')) return;
                setCamId(e.target.value);
              }}>
              {cameras.length === 0 && <option value="">Add a camera first</option>}
              {cameras.map(c => <option key={c.id} value={c.id}>{c.name} ({c.monitored_slots?.length || 0} slots)</option>)}
            </select>
            <button className="cctv-btn secondary small" onClick={() => loadFrame()} disabled={!camId || busy === 'frame'}>
              {busy === 'frame' ? <RefreshCw size={14} className="spin" /> : <CameraIcon size={14} />} Grab live frame
            </button>
            <button className="cctv-btn secondary small" onClick={() => fileRef.current?.click()}><Upload size={14} /> Use a screenshot</button>
            <span style={{ marginLeft: 'auto', fontSize: 12, color: dirty ? '#B45309' : '#64748B', fontWeight: 600 }}>
              {dirty ? '● Unsaved changes' : cam ? `${cam.monitored_slots?.length || 0} slots saved` : ''}
            </span>
            <button className="cctv-btn primary small" onClick={save} disabled={!camId || busy === 'save' || !dirty}>
              {busy === 'save' ? <RefreshCw size={14} className="spin" /> : <Save size={14} />} Save layout
            </button>
          </>
        )}
        <input ref={fileRef} type="file" accept="image/*" hidden onChange={e => { if (e.target.files?.[0]) onUpload(e.target.files[0]); e.target.value = ''; }} />
      </div>

      {msg && (
        <div className={`cctv-test-result ${msg.kind === 'err' ? 'error' : 'success'}`} style={msg.kind === 'info' ? { background: '#EFF6FF', color: '#1E40AF', borderColor: '#BFDBFE' } : undefined}>
          {msg.kind === 'err' ? <AlertTriangle size={16} /> : msg.kind === 'ok' ? <CheckCircle size={16} /> : <Info size={16} />}
          <span style={{ flex: 1 }}>{msg.text}</span>
          <button className="cctv-icon-btn" onClick={() => setMsg(null)}><X size={14} /></button>
        </div>
      )}

      <div className="multi-slot-workspace">
        {/* Canvas */}
        <div className="cctv-verify-card" style={{ gap: 10 }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <select className="cctv-input" value={detectOpt} onChange={e => setDetectOpt(e.target.value)} style={{ padding: '5px 8px', fontSize: 12 }} title="How to find the slots">
              {DETECT_OPTIONS.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
            <button className="cctv-btn small" onClick={detectSlots} disabled={!frame || busy === 'suggest'}
              style={{ background: '#7C3AED', color: '#fff' }}>
              {busy === 'suggest' ? <RefreshCw size={14} className="spin" /> : <Wand2 size={14} />} Detect slots
            </button>
            <span style={{ width: 1, height: 22, background: '#E2E8F0', margin: '0 2px' }} />
            {toolBtn('select', <MousePointer2 size={14} />, 'Edit', 'Select, drag corners or whole slots')}
            {toolBtn('slot', <PenTool size={14} />, 'Draw slot', 'Click the 4 floor corners of one slot')}
            {toolBtn('row', <Rows3 size={14} />, 'Draw row', 'Click the 4 corners of a whole row, it is split into equal slots')}
            {tool === 'row' && (
              <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center', fontSize: 12 }}>
                <input type="number" min={1} max={30} value={rowCount} onChange={e => setRowCount(Number(e.target.value) || 1)} className="cctv-input" style={{ width: 56, padding: '4px 6px' }} /> slots
              </span>
            )}
            <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center', fontSize: 12, color: '#475569' }} title="Slot ID given to the first new slot; the next ones follow in order">
              first slot
              <select value={rowStartId} onChange={e => setRowStartId(e.target.value)} className="cctv-input" style={{ width: 120, padding: '4px 6px' }}>
                <option value="">{suggestId() ? `auto (${nameOf(suggestId())})` : 'no free slot'}</option>
                {parkingSlots.map(p => <option key={p.id} value={p.id}>{p.label} · {p.floor_name || p.floor_id}</option>)}
              </select>
            </span>
            <span style={{ flex: 1 }} />
            {lines.length > 0 && (
              <button className="cctv-btn secondary small" onClick={() => setShowLines(v => !v)} title="Show the painted lines the AI found">
                {showLines ? <EyeOff size={14} /> : <Eye size={14} />} Lines
              </button>
            )}
            <button className="cctv-btn secondary small" onClick={undo} disabled={!history.length} title="Undo (Ctrl+Z)"><Undo2 size={14} /></button>
            {!isPhoto && (
              <button className="cctv-btn secondary small" onClick={check} disabled={!frame || !slots.length || busy === 'check'}>
                {busy === 'check' ? <RefreshCw size={14} className="spin" /> : <ScanSearch size={14} />} Test on this frame
              </button>
            )}
          </div>

          <div style={{ fontSize: 12, color: '#475569', background: '#F8FAFC', padding: '6px 10px', borderRadius: 6 }}>
            {tool === 'select' && (isPhoto && !frame
              ? 'Upload or take a photo of the parking area to start.'
              : 'Detect slots finds bays from yellow / blue / white floor lines. Drag a corner to reshape, drag inside a slot to move it. Delete removes, Ctrl+Z undoes.')}
            {tool === 'slot' && `Click the 4 corners of the slot on the floor (${draft.length}/4). Esc cancels.`}
            {tool === 'row' && `Click the 4 corners of the whole row: both ends of the front edge, then both ends of the back edge (${draft.length}/4). It will be split into ${rowCount} slots with perspective.`}
          </div>

          <div ref={wrapRef} style={{ position: 'relative', width: '100%', background: '#0F172A', borderRadius: 8, overflow: 'hidden', userSelect: 'none' }}>
            {frame ? (
              <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', display: 'block', touchAction: 'none', cursor: tool === 'select' ? 'default' : 'crosshair' }}
                onPointerDown={onSvgDown} onPointerMove={onSvgMove} onPointerUp={onSvgUp} onPointerLeave={() => { onSvgUp(); setHover(null); }}>
                <image href={frame} x={0} y={0} width={W} height={H} />
                {showLines && lines.map((l, i) => (
                  <line key={`ln${i}`} x1={l[0] * W} y1={l[1] * H} x2={l[2] * W} y2={l[3] * H} stroke="#22D3EE" strokeWidth={2 * k} strokeDasharray={`${4 * k},${3 * k}`} style={{ pointerEvents: 'none' }} />
                ))}
                {slots.map((s, i) => {
                  const r = bySlot[s.slot_id.toUpperCase()];
                  const color = r ? VERDICT_STROKE[r.verification_status] ?? '#94A3B8' : SLOT_COLORS[i % SLOT_COLORS.length];
                  const pts = s.points.map(p => `${p[0] * W},${p[1] * H}`).join(' ');
                  const cx = (s.points.reduce((a, p) => a + p[0], 0) / s.points.length) * W;
                  const cy = (s.points.reduce((a, p) => a + p[1], 0) / s.points.length) * H;
                  const active = sel === i;
                  const isViol = r?.violation_type;
                  const sub = r ? (r.detected_plate || (r.occupancy_status === 'occupied' ? 'car · plate?' : 'empty')) : null;
                  const boxW = Math.max(68, (sub?.length || 0) * 7.2 + 12) * k;
                  return (
                    <g key={i}>
                      <polygon points={pts} fill={color} fillOpacity={isViol ? 0.35 : active ? 0.32 : 0.18} stroke={color}
                        strokeWidth={(active || isViol ? 3.5 : 2) * k} strokeDasharray={active || r ? undefined : `${6 * k},${3 * k}`}
                        style={{ cursor: tool === 'select' ? 'move' : 'crosshair' }}
                        onPointerDown={e => (tool === 'select' ? startPolyDrag(e, i) : undefined)} />
                      {r?.vehicle_box && (
                        <rect x={r.vehicle_box[0] * (W / (result!.image_size[0] || W))} y={r.vehicle_box[1] * (H / (result!.image_size[1] || H))}
                          width={(r.vehicle_box[2] - r.vehicle_box[0]) * (W / (result!.image_size[0] || W))}
                          height={(r.vehicle_box[3] - r.vehicle_box[1]) * (H / (result!.image_size[1] || H))}
                          fill="none" stroke={color} strokeWidth={1.5 * k} style={{ pointerEvents: 'none' }} />
                      )}
                      <g style={{ pointerEvents: 'none' }}>
                        <rect x={cx - boxW / 2} y={cy - 13 * k} width={boxW} height={sub ? 40 * k : 24 * k} rx={4 * k}
                          fill={isViol ? 'rgba(185,28,28,0.92)' : 'rgba(15,23,42,0.82)'} />
                        <text x={cx} y={cy + 4 * k} textAnchor="middle" fill="#fff" fontSize={13 * k} fontWeight={700}>{nameOf(s.slot_id) || '?'}</text>
                        {sub && <text x={cx} y={cy + 20 * k} textAnchor="middle" fill="#FDE68A" fontSize={11 * k} fontWeight={600}>{sub}</text>}
                      </g>
                      {tool === 'select' && !r && s.points.map((p, j) => (
                        <circle key={j} cx={p[0] * W} cy={p[1] * H} r={active ? handleR * 1.3 : handleR} fill={color} stroke="#fff" strokeWidth={2 * k}
                          style={{ cursor: 'grab' }} onPointerDown={e => startVertexDrag(e, i, j)} />
                      ))}
                    </g>
                  );
                })}
                {draft.length > 0 && (
                  <g style={{ pointerEvents: 'none' }}>
                    <polyline points={[...draft, ...(hover ? [hover] : [])].map(p => `${p[0] * W},${p[1] * H}`).join(' ')}
                      fill="rgba(245,158,11,0.15)" stroke="#F59E0B" strokeWidth={2 * k} strokeDasharray={`${5 * k},${3 * k}`} />
                    {tool === 'row' && draft.length === 3 && hover && (() => {
                      const map = squareToQuad([...draft, hover]);
                      return Array.from({ length: Math.max(0, rowCount - 1) }, (_, i) => {
                        const u = (i + 1) / rowCount;
                        const a = map(u, 0), b = map(u, 1);
                        return <line key={i} x1={a[0] * W} y1={a[1] * H} x2={b[0] * W} y2={b[1] * H} stroke="#F59E0B" strokeWidth={1.5 * k} />;
                      });
                    })()}
                    {draft.map((p, i) => <circle key={i} cx={p[0] * W} cy={p[1] * H} r={handleR} fill="#F59E0B" stroke="#fff" strokeWidth={2 * k} />)}
                  </g>
                )}
              </svg>
            ) : (
              <div style={{ color: '#94A3B8', textAlign: 'center', padding: '80px 20px', cursor: isPhoto ? 'pointer' : 'default' }}
                onClick={() => isPhoto && fileRef.current?.click()}>
                {isPhoto ? <Upload size={40} style={{ opacity: 0.5 }} /> : <CameraIcon size={40} style={{ opacity: 0.5 }} />}
                <p style={{ margin: '8px 0 4px', fontWeight: 600 }}>{isPhoto ? 'Upload a parking photo' : 'No frame loaded'}</p>
                <span style={{ fontSize: 12 }}>
                  {isPhoto ? 'A photo of a parking row (yellow/blue lines work best) or a close-up of one car with its number plate.'
                    : busy === 'frame' ? 'Grabbing a frame from the camera…' : 'Grab a live frame from the camera (it must be running) or upload a screenshot from it.'}
                </span>
              </div>
            )}
            {busy === 'check' && (
              <div style={{ position: 'absolute', inset: 0, background: 'rgba(15,23,42,.55)', color: '#fff', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, fontWeight: 600 }}>
                <RefreshCw size={30} className="spin" />
                Detecting vehicles and reading number plates… {elapsed}s
              </div>
            )}
          </div>
          {result && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, color: '#475569' }}>
              Showing AI result on the photo. <button className="cctv-btn secondary small" onClick={() => setResult(null)}>Edit slots again</button>
            </div>
          )}
        </div>

        {/* Slot list */}
        <div className="cctv-verify-card" style={{ gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <h4 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{isPhoto ? 'Slots in this photo' : 'Slots in this camera'} ({slots.length})</h4>
            {slots.length > 0 && (
              <button className="cctv-btn danger small" onClick={() => { if (window.confirm('Remove all slots?')) { commit([]); setSel(null); setLines([]); } }}>
                <Trash2 size={13} /> Clear
              </button>
            )}
          </div>

          {issues.length > 0 && (
            <div style={{ background: '#FFFBEB', border: '1px solid #FDE68A', color: '#92400E', fontSize: 12, borderRadius: 8, padding: '8px 10px' }}>
              {issues.map(i => <div key={i}>⚠ {i}</div>)}
            </div>
          )}

          {slots.length === 0 ? (
            <div style={{ fontSize: 13, color: '#64748B', lineHeight: 1.6 }}>
              No slots yet. Press <strong>Detect slots</strong> to find bays from the floor lines, use <strong>Draw row</strong> for a whole row
              {isPhoto ? <>, or <strong>Whole photo = 1 slot</strong> for a close-up of one car.</> : <> or <strong>Draw slot</strong> for single bays.</>}
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 520, overflowY: 'auto' }}>
              {slots.map((s, i) => {
                const info = parkingSlots.find(p => p.id.toUpperCase() === s.slot_id.toUpperCase());
                const r = bySlot[s.slot_id.toUpperCase()];
                const color = r ? VERDICT_STROKE[r.verification_status] ?? '#94A3B8' : SLOT_COLORS[i % SLOT_COLORS.length];
                return (
                  <div key={i} onClick={() => setSel(i)}
                    style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '8px 10px', borderRadius: 8, cursor: 'pointer',
                      border: `1.5px solid ${sel === i || r?.violation_type ? color : '#E2E8F0'}`, background: r?.violation_type ? '#FEF2F2' : sel === i ? '#F8FAFC' : '#fff' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ width: 12, height: 12, borderRadius: 3, background: color, flexShrink: 0 }} />
                      <select value={info ? info.id : s.slot_id} onChange={e => updateId(i, e.target.value)} onClick={e => e.stopPropagation()}
                        className="cctv-input" style={{ flex: 1, minWidth: 0, padding: '4px 6px', fontWeight: 700 }}>
                        {!info && <option value={s.slot_id}>{s.slot_id ? `${s.slot_id} (pick a slot)` : 'Pick a slot…'}</option>}
                        {parkingSlots.map(p => <option key={p.id} value={p.id}>{slotOptionText(p)}</option>)}
                      </select>
                      <button className="cctv-icon-btn" title="Duplicate (placed next to it)" onClick={e => {
                        e.stopPropagation();
                        const xs = s.points.map(p => p[0]);
                        const dx = Math.max(...xs) - Math.min(...xs);
                        const id = suggestId();
                        if (!id) { setMsg({ kind: 'err', text: NO_FREE_SLOT }); return; }
                        commit([...slots, { slot_id: id, label: id, points: s.points.map(p => [r3(p[0] + dx), p[1]]) }]);
                        setSel(slots.length);
                      }}><Copy size={14} /></button>
                      <button className="cctv-icon-btn" title="Delete" onClick={e => { e.stopPropagation(); commit(slots.filter((_, j) => j !== i)); setSel(null); }}><Trash2 size={14} /></button>
                    </div>
                    {r && (
                      <div style={{ fontSize: 11, color: '#334155' }}>
                        <strong style={{ color }}>{r.violation_type ? 'VIOLATION' : r.verification_status.replace(/_/g, ' ')}</strong>
                        {r.detected_plate ? ` · ${r.detected_plate}` : ''}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div style={{ marginTop: 'auto', fontSize: 11, color: '#64748B', lineHeight: 1.5, borderTop: '1px solid #F1F5F9', paddingTop: 8 }}>
            Tip: slot outlines should follow the painted bay lines on the floor, where the tyres sit. Pick each slot's name from the list (names come from Admin → Parking Lots) so bookings and reserved number plates can be checked.
          </div>
        </div>
      </div>

      {result && (
        <div ref={resultsRef}>
          <PhotoResults result={result} onOpenViolation={onOpenViolation} />
        </div>
      )}
    </div>
  );
}
