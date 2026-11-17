import { useEffect, useState } from 'react';
import { Video, RefreshCw, LayoutGrid, AlertTriangle, Camera as CameraIcon, User, ImageOff } from 'lucide-react';
import { ZoomImage } from './ZoomImage';
import { LiveFrame } from './LiveFrame';
import { cctvApi, VERDICT_STYLE, VIOLATION_LABEL, fmtDuration, type Camera, type LiveSlot, type LiveStatus } from './cctvApi';

function SlotCard({ s, onOpenViolation }: { s: LiveSlot; onOpenViolation: (id: string) => void }) {
  const vs = VERDICT_STYLE[s.verification_status] ?? VERDICT_STYLE.DETECTING;
  const occupied = s.occupancy_status === 'occupied';
  const isViolation = s.verification_status === 'WRONG_SLOT' || s.verification_status === 'UNAUTHORIZED';
  return (
    <div style={{ background: '#fff', border: `1.5px solid ${vs.border}`, borderRadius: 10, padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 16, fontWeight: 800, color: '#0F172A' }}>{s.slot_name || s.slot_id}</span>
        <span style={{ fontSize: 11, fontWeight: 700, color: vs.color, background: vs.bg, padding: '3px 8px', borderRadius: 6 }}>
          {isViolation && s.violation_type ? VIOLATION_LABEL[s.violation_type] ?? vs.label : vs.label}
        </span>
        {occupied && <span style={{ fontSize: 11, color: '#64748B' }}>parked {fmtDuration(s.occupied_seconds)}</span>}
        {s.violation_id && (
          <button className="cctv-btn danger small" style={{ marginLeft: 'auto', padding: '3px 8px' }} onClick={() => onOpenViolation(s.violation_id!)}>
            <AlertTriangle size={12} /> View violation
          </button>
        )}
      </div>

      {occupied && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          {s.plate_crop_base64 ? (
            <ZoomImage src={s.plate_crop_base64} alt="plate" caption={s.detected_plate || s.raw_plate || undefined} style={{ height: 38, maxWidth: 160, objectFit: 'contain', borderRadius: 4, border: '1px solid #CBD5E1', background: '#fff' }} />
          ) : (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: '#94A3B8' }}><ImageOff size={14} /> no plate crop</span>
          )}
          <div>
            <div style={{ fontFamily: 'monospace', fontWeight: 800, fontSize: 15, color: '#1E3A5F' }}>
              {s.detected_plate || (s.raw_plate ? `${s.raw_plate}?` : '— — —')}
            </div>
            <div style={{ fontSize: 11, color: '#64748B' }}>
              {s.detected_plate
                ? `${Math.round(s.confidence * 100)}% · ${s.plate_reads} reads${s.plate_match === 'fuzzy' ? ' · approximate match, check image' : ''}`
                : s.unreadable_reason || `${s.plate_reads} reads so far`}
              {s.occupancy_source === 'appearance' && ' · seen by empty-slot comparison'}
            </div>
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 12, background: '#F8FAFC', borderRadius: 8, padding: '8px 10px' }}>
        <div>
          <div style={{ color: '#64748B' }}>Booked for today</div>
          <div style={{ fontWeight: 700, color: '#0F172A' }}>{s.expected_user || 'Nobody'}</div>
          <div style={{ fontFamily: 'monospace', fontSize: 11, color: '#475569' }}>
            {s.expected_plate || '—'}{s.expected_source === 'manager_assigned' ? ' · reserved slot' : ''}
          </div>
        </div>
        <div>
          <div style={{ color: '#64748B' }}>Vehicle owner</div>
          {s.matched_user ? (
            <>
              <div style={{ fontWeight: 700, color: '#0F172A', display: 'flex', alignItems: 'center', gap: 4 }}><User size={12} /> {s.matched_user.name}</div>
              <div style={{ fontSize: 11, color: '#475569' }}>
                {s.matched_user.department || s.matched_user.role}
                {s.matched_user.reserved_slot_today ? ` · ${s.matched_user.slot_is_permanent ? 'reserved slot' : 'booked'} ${s.matched_user.reserved_slot_today}` : ' · no booking today'}
              </div>
            </>
          ) : (
            <div style={{ fontWeight: 600, color: '#64748B' }}>{s.detected_plate ? 'Not a registered vehicle' : '—'}</div>
          )}
        </div>
      </div>
      <div style={{ fontSize: 12, color: vs.color }}>{s.message}</div>
    </div>
  );
}

export function LiveMonitor({ cameras, initialCameraId, onOpenLayout, onOpenViolation }: {
  cameras: Camera[];
  initialCameraId?: string | null;
  onOpenLayout: (id: string) => void;
  onOpenViolation: (id: string) => void;
}) {
  const [camId, setCamId] = useState<string>(initialCameraId || cameras[0]?.id || '');
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [nonce, setNonce] = useState(0);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => { if (initialCameraId) setCamId(initialCameraId); }, [initialCameraId]);
  useEffect(() => { if (!camId && cameras[0]) setCamId(cameras[0].id); }, [cameras, camId]);

  useEffect(() => {
    if (!camId) return;
    let alive = true;
    const tick = async () => {
      try { const s = await cctvApi.liveStatus(camId); if (alive) setStatus(s); }
      catch { if (alive) setStatus(null); }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [camId]);


  const cam = cameras.find(c => c.id === camId);

  if (!cameras.length) {
    return (
      <div className="cctv-empty-state">
        <Video size={36} />
        <p>No cameras yet</p>
        <span>Add a camera in the Cameras tab, then draw its slot layout.</span>
      </div>
    );
  }

  const baseline = async (clear = false) => {
    try { const r = await cctvApi.captureBaseline(camId, clear); setNote(r.message); }
    catch (e: any) { setNote(e.message); }
    setTimeout(() => setNote(null), 5000);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {cameras.map(c => (
          <button key={c.id} onClick={() => setCamId(c.id)} className={`cctv-preset-btn ${camId === c.id ? 'active' : ''}`}
            style={camId === c.id ? { background: '#1E3A5F', color: '#fff' } : undefined}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: c.status === 'connected' ? '#22C55E' : c.status === 'stopped' ? '#94A3B8' : '#F59E0B' }} />
            {c.name} <span style={{ opacity: 0.7 }}>({c.monitored_slots?.length || 0})</span>
          </button>
        ))}
      </div>

      <div className="multi-slot-workspace">
        <div className="cctv-verify-card" style={{ gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <strong style={{ fontSize: 14 }}>{cam?.name}</strong>
            {status && (
              <span className="cctv-fps-badge">
                {status.stream_status === 'connected' ? `${status.resolution} · ${status.fps} fps` : status.stream_status}
              </span>
            )}
            {status?.running && status.last_cycle_ms ? <span style={{ fontSize: 11, color: '#64748B' }}>AI cycle {Math.round(status.last_cycle_ms)} ms</span> : null}
            <span style={{ flex: 1 }} />
            <button className="cctv-btn secondary small" onClick={() => setNonce(n => n + 1)}><RefreshCw size={13} /> Reconnect</button>
            <button className="cctv-btn secondary small" onClick={() => onOpenLayout(camId)}><LayoutGrid size={13} /> Edit layout</button>
          </div>

          <div style={{ position: 'relative', background: '#0F172A', borderRadius: 8, overflow: 'hidden', minHeight: 260, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {cam && cam.status !== 'stopped' ? (
              <LiveFrame key={`${camId}-${nonce}`} cameraId={camId} alt={`${cam.name} live`} />
            ) : (
              <div style={{ color: '#94A3B8', textAlign: 'center', padding: 40 }}>
                <CameraIcon size={40} style={{ opacity: 0.5 }} />
                <p>{cam?.status === 'stopped' ? 'Camera is stopped – start it in the Cameras tab' : 'Connecting…'}</p>
              </div>
            )}
          </div>

          {(status?.stream_error && status.stream_status !== 'connected') && (
            <div className="cctv-test-result error"><AlertTriangle size={16} /><span>{status.stream_error}</span></div>
          )}
          {status?.last_error && <div className="cctv-test-result error"><AlertTriangle size={16} /><span>AI monitor: {status.last_error}</span></div>}

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 12, color: '#475569', background: '#F8FAFC', borderRadius: 8, padding: '8px 10px' }}>
            <span style={{ flex: '1 1 260px' }}>
              <strong>Empty-slot baseline:</strong> when all slots are empty, capture it once – the monitor then also catches cars the detector misses (odd angles, partial views).
              {status?.slots?.length ? ` ${status.slots.filter(s => s.has_empty_baseline).length}/${status.slots.length} slots have one.` : ''}
            </span>
            <button className="cctv-btn secondary small" onClick={() => baseline(false)} disabled={!status?.running}>Capture now</button>
            <button className="cctv-btn secondary small" onClick={() => baseline(true)} disabled={!status?.running}>Clear</button>
          </div>
          {note && <div style={{ fontSize: 12, color: '#1E40AF' }}>{note}</div>}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, background: '#fff', border: '1px solid #E2E8F0', borderRadius: 10, padding: 10, textAlign: 'center' }}>
            {[
              ['Slots', status?.total_slots ?? 0, '#1E3A5F'],
              ['Occupied', status?.occupied_count ?? 0, '#B45309'],
              ['Vacant', status?.vacant_count ?? 0, '#047857'],
              ['Violations', status?.violation_count ?? 0, '#DC2626'],
            ].map(([l, v, c]) => (
              <div key={l as string}>
                <div style={{ fontSize: 20, fontWeight: 800, color: c as string }}>{v as number}</div>
                <div style={{ fontSize: 11, color: '#64748B', fontWeight: 600 }}>{l as string}</div>
              </div>
            ))}
          </div>

          {!cam?.monitored_slots?.length ? (
            <div className="cctv-empty-state" style={{ padding: 24 }}>
              <LayoutGrid size={32} />
              <p>No slot layout for this camera</p>
              <span>Draw which slots this camera covers – only those areas are analysed.</span>
              <button className="cctv-btn primary small" style={{ marginTop: 10 }} onClick={() => onOpenLayout(camId)}>Draw slot layout</button>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 640, overflowY: 'auto' }}>
              {(status?.slots ?? []).map(s => <SlotCard key={s.slot_id} s={s} onOpenViolation={onOpenViolation} />)}
              {status && !status.slots.length && <div style={{ fontSize: 13, color: '#64748B' }}>Waiting for the first analysis…</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
