import { useCallback, useEffect, useState } from 'react';
import { AlertOctagon, RefreshCw, Search, CheckCircle, XCircle, RotateCcw, Trash2, User, X, Clock, Car, CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { ZoomImage } from './ZoomImage';
import { cctvApi, mediaUrl, VIOLATION_LABEL, fmtTime, fmtDuration, type Violation } from './cctvApi';

const TYPE_STYLE: Record<string, [string, string]> = {
  WRONG_SLOT: ['#9A3412', '#FFEDD5'],
  NO_BOOKING: ['#B45309', '#FEF3C7'],
  UNKNOWN_VEHICLE: ['#991B1B', '#FEE2E2'],
  UNREADABLE_PLATE: ['#475569', '#F1F5F9'],
};
const STATUS_STYLE: Record<string, [string, string, string]> = {
  open: ['Open', '#B91C1C', '#FEF2F2'],
  resolved: ['Resolved', '#047857', '#ECFDF5'],
  false_alarm: ['False alarm', '#475569', '#F1F5F9'],
};

function stayText(v: Violation) {
  if (!v.occupied_since) return null;
  const start = new Date(v.occupied_since).getTime();
  const end = v.vehicle_left_at ? new Date(v.vehicle_left_at).getTime() : Date.now();
  if (isNaN(start) || isNaN(end)) return null;
  const d = fmtDuration(Math.max(0, Math.round((end - start) / 1000)));
  return v.vehicle_left_at ? `stayed ${d}` : `parked ${d} (still there)`;
}

function Badge({ text, color, bg }: { text: string; color: string; bg: string }) {
  return <span style={{ fontSize: 11, fontWeight: 700, color, background: bg, padding: '3px 8px', borderRadius: 6, whiteSpace: 'nowrap' }}>{text}</span>;
}

function Detail({ v, onClose, onChanged }: { v: Violation; onClose: () => void; onChanged: (v: Violation | null) => void }) {
  const [plate, setPlate] = useState(v.corrected_plate || '');
  const [notes, setNotes] = useState(v.notes || '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [zoom, setZoom] = useState<string | null>(null);

  const update = async (body: Parameters<typeof cctvApi.updateViolation>[1]) => {
    setBusy(true); setErr(null);
    try { onChanged(await cctvApi.updateViolation(v.id, body)); } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  const mu = v.matched_user;
  const [tc, tb] = TYPE_STYLE[v.violation_type] ?? ['#475569', '#F1F5F9'];
  const [sl, sc, sb] = STATUS_STYLE[v.status] ?? STATUS_STYLE.open;

  return (
    <div className="cctv-modal-overlay" onClick={onClose}>
      <div className="cctv-modal" style={{ maxWidth: 980, width: '96vw', maxHeight: '92vh', overflowY: 'auto' }} onClick={e => e.stopPropagation()}>
        <div className="cctv-modal-header">
          <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <AlertOctagon size={18} color="#DC2626" /> Slot {v.slot_label || v.slot_id}
            <Badge text={VIOLATION_LABEL[v.violation_type] ?? v.violation_type} color={tc} bg={tb} />
            <Badge text={sl} color={sc} bg={sb} />
          </h3>
          <button onClick={onClose} className="cctv-icon-btn"><X size={20} /></button>
        </div>

        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 14, color: '#0F172A', fontWeight: 500 }}>{v.message}</div>

          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,2fr) minmax(0,1fr)', gap: 10 }} className="vio-evidence">
            {v.snapshot_url ? (
              <img src={mediaUrl(v.snapshot_url)} alt="Scene" onClick={() => setZoom(mediaUrl(v.snapshot_url))} style={{ width: '100%', borderRadius: 8, cursor: 'zoom-in', border: '1px solid #E2E8F0' }} />
            ) : <div className="cctv-empty-state">No scene image</div>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {v.plate_crop_url && (
                <div>
                  <div style={{ fontSize: 11, color: '#64748B', marginBottom: 4 }}>Number plate</div>
                  <img src={mediaUrl(v.plate_crop_url)} alt="Plate" onClick={() => setZoom(mediaUrl(v.plate_crop_url))} style={{ width: '100%', borderRadius: 6, border: '1px solid #CBD5E1', cursor: 'zoom-in', background: '#fff' }} />
                </div>
              )}
              {v.vehicle_crop_url && (
                <div>
                  <div style={{ fontSize: 11, color: '#64748B', marginBottom: 4 }}>Vehicle</div>
                  <img src={mediaUrl(v.vehicle_crop_url)} alt="Vehicle" onClick={() => setZoom(mediaUrl(v.vehicle_crop_url))} style={{ width: '100%', borderRadius: 6, border: '1px solid #E2E8F0', cursor: 'zoom-in' }} />
                </div>
              )}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 10 }}>
            <div style={{ background: '#F8FAFC', borderRadius: 8, padding: 12, fontSize: 13, display: 'grid', gap: 4 }}>
              <strong style={{ fontSize: 12, color: '#64748B' }}>DETECTED VEHICLE</strong>
              <span style={{ fontFamily: 'monospace', fontSize: 18, fontWeight: 800, color: '#1E3A5F' }}>
                {v.corrected_plate || v.detected_plate || 'Unreadable'}
              </span>
              {v.corrected_plate && <span style={{ fontSize: 11, color: '#B45309' }}>Corrected by admin (AI read {v.detected_plate || 'nothing'})</span>}
              <span style={{ fontSize: 12, color: '#475569' }}>
                {v.plate_confidence != null && `${Math.round((v.plate_confidence || 0) * 100)}% confidence · `}{v.plate_reads ?? 0} matching reads
                {v.plate_match === 'fuzzy' && ` · approximate match to a known plate (AI read ${v.raw_plate}) – check the image`}
              </span>
              <span style={{ fontSize: 12, color: '#475569' }}><Clock size={11} /> {fmtTime(v.created_at)} · {stayText(v) || ''}</span>
              <span style={{ fontSize: 12, color: '#475569' }}>{v.camera_name}{v.floor ? ` · ${v.floor}` : ''}</span>
            </div>

            <div style={{ background: mu ? '#FFF7ED' : '#F8FAFC', borderRadius: 8, padding: 12, fontSize: 13, display: 'grid', gap: 3 }}>
              <strong style={{ fontSize: 12, color: '#64748B' }}>VEHICLE OWNER</strong>
              {mu ? (
                <>
                  <span style={{ fontWeight: 700, display: 'flex', alignItems: 'center', gap: 5 }}><User size={14} /> {mu.name}</span>
                  {mu.employee_id && <span>Employee ID: {mu.employee_id}</span>}
                  <span>{[mu.department, mu.role].filter(Boolean).join(' · ')}</span>
                  {mu.email && <span>{mu.email}</span>}
                  {mu.mobile_number && <span>{mu.mobile_number}</span>}
                  <span>Registered vehicle: <span style={{ fontFamily: 'monospace' }}>{mu.vehicle_number || '—'}</span></span>
                  <span style={{ fontWeight: 600 }}>{mu.slot_is_permanent ? 'Reserved slot' : 'Booking today'}: {mu.reserved_slot_today ? `slot ${mu.reserved_slot_today}` : 'none'}</span>
                </>
              ) : (
                <span style={{ color: '#64748B' }}>No employee has this vehicle registered.</span>
              )}
            </div>

            <div style={{ background: '#F8FAFC', borderRadius: 8, padding: 12, fontSize: 13, display: 'grid', gap: 3 }}>
              <strong style={{ fontSize: 12, color: '#64748B' }}>SLOT {v.slot_label || v.slot_id} WAS FOR</strong>
              {v.expected?.user_name ? (
                <>
                  <span style={{ fontWeight: 700 }}>{v.expected.user_name}</span>
                  <span style={{ fontFamily: 'monospace' }}>{v.expected.vehicle_number || '—'}</span>
                  {v.expected.email && <span>{v.expected.email}</span>}
                  <span style={{ color: '#64748B' }}>{v.expected.source === 'manager_assigned' ? 'Permanently reserved slot' : 'Reservation for today'}</span>
                </>
              ) : <span style={{ color: '#64748B' }}>Nobody had booked this slot.</span>}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 10, alignItems: 'end' }}>
            <label style={{ fontSize: 12, color: '#475569', display: 'grid', gap: 4 }}>
              Correct the plate (re-checks the owner)
              <span style={{ display: 'flex', gap: 6 }}>
                <input className="cctv-input" value={plate} onChange={e => setPlate(e.target.value.toUpperCase())} placeholder="e.g. MH 12 AB 1234" style={{ fontFamily: 'monospace', flex: 1 }} />
                <button className="cctv-btn secondary small" disabled={busy || !plate.trim()} onClick={() => update({ corrected_plate: plate })}>Apply</button>
              </span>
            </label>
            <label style={{ fontSize: 12, color: '#475569', display: 'grid', gap: 4 }}>
              Notes
              <span style={{ display: 'flex', gap: 6 }}>
                <input className="cctv-input" value={notes} onChange={e => setNotes(e.target.value)} placeholder="Action taken…" style={{ flex: 1 }} />
                <button className="cctv-btn secondary small" disabled={busy} onClick={() => update({ notes })}>Save</button>
              </span>
            </label>
          </div>
          {err && <div className="cctv-test-result error">{err}</div>}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', borderTop: '1px solid #F1F5F9', paddingTop: 12 }}>
            {v.status !== 'resolved' && <button className="cctv-btn primary" disabled={busy} onClick={() => update({ status: 'resolved', notes })}><CheckCircle size={14} /> Mark resolved</button>}
            {v.status !== 'false_alarm' && <button className="cctv-btn secondary" disabled={busy} onClick={() => update({ status: 'false_alarm', notes })}><XCircle size={14} /> False alarm</button>}
            {v.status !== 'open' && <button className="cctv-btn secondary" disabled={busy} onClick={() => update({ status: 'open' })}><RotateCcw size={14} /> Reopen</button>}
            <span style={{ flex: 1 }} />
            <button className="cctv-btn danger" disabled={busy} onClick={async () => {
              if (!window.confirm('Delete this violation and its images permanently?')) return;
              try { await cctvApi.deleteViolation(v.id); onChanged(null); onClose(); } catch (e: any) { setErr(e.message); }
            }}><Trash2 size={14} /> Delete</button>
          </div>
        </div>

        {zoom && (
          <div className="cctv-modal-overlay" style={{ zIndex: 2000 }} onClick={() => setZoom(null)}>
            <img src={zoom} alt="Evidence" style={{ maxWidth: '95vw', maxHeight: '92vh', borderRadius: 8 }} />
          </div>
        )}
      </div>
    </div>
  );
}

export function ViolationsLog({ focusId, onCountChange }: { focusId?: string | null; onCountChange?: (open: number) => void }) {
  const [items, setItems] = useState<Violation[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [status, setStatus] = useState<string>('open');
  const [plate, setPlate] = useState('');
  const [slot, setSlot] = useState('');
  const today = () => new Date().toLocaleDateString('en-CA');  // YYYY-MM-DD in local time
  const [range, setRange] = useState<'day' | 'range' | 'all'>('all');
  const [day, setDay] = useState(today());
  const [from, setFrom] = useState(() => { const d = new Date(); d.setDate(d.getDate() - 6); return d.toLocaleDateString('en-CA'); });
  const [to, setTo] = useState(today());
  const shiftDay = (n: number) => { const d = new Date(day + 'T12:00:00'); d.setDate(d.getDate() + n); setDay(d.toLocaleDateString('en-CA')); };
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<Violation | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const dates = range === 'day' ? { date: day } : range === 'range' ? { date_from: from, date_to: to } : {};
      const r = await cctvApi.violations({ status: status === 'all' ? undefined : status, plate, slot_id: slot, ...dates, limit: 200 });
      setItems(r.items); setTotal(r.total); setCounts(r.status_counts); setErr(null);
      onCountChange?.(r.status_counts.open || 0);
    } catch (e: any) { setErr(e.message); }
    setLoading(false);
  }, [status, plate, slot, range, day, from, to, onCountChange]);

  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  useEffect(() => {
    if (!focusId) return;
    const hit = items.find(i => i.id === focusId);
    if (hit) setOpen(hit);
    else cctvApi.violation(focusId).then(v => v && setOpen(v)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="cctv-verify-card" style={{ padding: '10px 14px', flexDirection: 'row', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        {[['open', 'Open'], ['resolved', 'Resolved'], ['false_alarm', 'False alarm'], ['all', 'All']].map(([k, l]) => (
          <button key={k} onClick={() => setStatus(k)} className={`cctv-preset-btn ${status === k ? 'active' : ''}`}
            style={status === k ? { background: '#1E3A5F', color: '#fff' } : undefined}>
            {l}{k !== 'all' ? ` (${counts[k] || 0})` : ''}
          </button>
        ))}
        <span style={{ flex: 1 }} />
        <div className="cctv-search-box"><Search size={14} /><input value={plate} onChange={e => setPlate(e.target.value)} placeholder="Plate…" /></div>
        <input className="cctv-input" value={slot} onChange={e => setSlot(e.target.value.toUpperCase())} placeholder="Slot" style={{ width: 80, padding: '6px 8px' }} />
        <button className="cctv-icon-btn" onClick={load} title="Refresh"><RefreshCw size={16} className={loading ? 'spin' : ''} /></button>
      </div>

      {/* Date filter: one day (with previous / next), a range, or everything */}
      <div className="cctv-verify-card" style={{ padding: '10px 14px', flexDirection: 'row', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        <CalendarDays size={16} color="#475569" />
        {([['all', 'All dates'], ['day', 'One day'], ['range', 'Date range']] as const).map(([k, l]) => (
          <button key={k} onClick={() => setRange(k)} className={`cctv-preset-btn ${range === k ? 'active' : ''}`}
            style={range === k ? { background: '#1E3A5F', color: '#fff' } : undefined}>{l}</button>
        ))}
        {range === 'day' && (
          <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <button className="cctv-icon-btn" onClick={() => shiftDay(-1)} title="Previous day"><ChevronLeft size={16} /></button>
            <input className="cctv-input" type="date" value={day} max={today()} onChange={e => e.target.value && setDay(e.target.value)} style={{ padding: '5px 8px' }} />
            <button className="cctv-icon-btn" onClick={() => shiftDay(1)} disabled={day >= today()} title="Next day"><ChevronRight size={16} /></button>
            {day !== today() && <button className="cctv-btn secondary small" onClick={() => setDay(today())}>Today</button>}
          </span>
        )}
        {range === 'range' && (
          <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <input className="cctv-input" type="date" value={from} max={to} onChange={e => e.target.value && setFrom(e.target.value)} style={{ padding: '5px 8px' }} />
            <span style={{ fontSize: 12, color: '#64748B' }}>to</span>
            <input className="cctv-input" type="date" value={to} min={from} max={today()} onChange={e => e.target.value && setTo(e.target.value)} style={{ padding: '5px 8px' }} />
            {[['7 days', 6], ['30 days', 29]].map(([l, n]) => (
              <button key={l as string} className="cctv-btn secondary small" onClick={() => {
                const d = new Date(); d.setDate(d.getDate() - (n as number)); setFrom(d.toLocaleDateString('en-CA')); setTo(today());
              }}>Last {l}</button>
            ))}
          </span>
        )}
      </div>

      {err && <div className="cctv-test-result error">{err}</div>}

      {items.length === 0 ? (
        <div className="cctv-empty-state">
          <Car size={34} />
          <p>{status === 'open' ? 'No open violations' : 'No violations found'}</p>
          <span>When a car is parked in a slot it isn't booked for, it appears here with photos and the owner's details.</span>
        </div>
      ) : (
        <>
          <div style={{ fontSize: 12, color: '#64748B' }}>{total} violation{total === 1 ? '' : 's'}</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
            {items.map(v => {
              const [tc, tb] = TYPE_STYLE[v.violation_type] ?? ['#475569', '#F1F5F9'];
              const [sl, sc, sb] = STATUS_STYLE[v.status] ?? STATUS_STYLE.open;
              return (
                <div key={v.id} onClick={() => setOpen(v)} style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 10, overflow: 'hidden', cursor: 'pointer', display: 'flex', flexDirection: 'column' }}>
                  <div style={{ position: 'relative', aspectRatio: '16 / 9', background: '#0F172A' }}>
                    {v.snapshot_url && <img src={mediaUrl(v.snapshot_url)} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />}
                    {v.plate_crop_url && (
                      <span style={{ position: 'absolute', right: 8, bottom: 8, maxWidth: '55%' }}>
                        <ZoomImage src={mediaUrl(v.plate_crop_url)} alt="plate" caption={v.corrected_plate || v.detected_plate || undefined}
                          style={{ height: 38, maxWidth: '100%', objectFit: 'contain', background: '#fff', borderRadius: 4, border: '2px solid #fff', boxShadow: '0 1px 4px rgba(0,0,0,.4)' }} />
                      </span>
                    )}
                    <span style={{ position: 'absolute', left: 8, top: 8 }}><Badge text={VIOLATION_LABEL[v.violation_type] ?? v.violation_type} color={tc} bg={tb} /></span>
                  </div>
                  <div style={{ padding: 10, display: 'grid', gap: 4, fontSize: 12 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontFamily: 'monospace', fontWeight: 800, fontSize: 15, color: '#1E3A5F' }}>{v.corrected_plate || v.detected_plate || 'Unreadable'}</span>
                      <span style={{ marginLeft: 'auto' }}><Badge text={sl} color={sc} bg={sb} /></span>
                    </div>
                    <div><strong>Slot {v.slot_label || v.slot_id}</strong>{v.floor ? ` · ${v.floor}` : ''} · {v.camera_name}</div>
                    <div style={{ color: '#475569' }}>
                      {v.matched_user ? <><User size={11} /> {v.matched_user.name}{v.matched_user.reserved_slot_today ? ` (${v.matched_user.slot_is_permanent ? 'reserved' : 'booked'} ${v.matched_user.reserved_slot_today})` : ' (no booking)'}</> : 'Unregistered vehicle'}
                      {' · '}slot for: {v.expected?.user_name || 'nobody'}
                    </div>
                    <div style={{ color: '#94A3B8' }}>{fmtTime(v.created_at)}{stayText(v) ? ` · ${stayText(v)}` : ''}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {open && (
        <Detail v={open} onClose={() => setOpen(null)} onChanged={nv => { if (nv) setOpen(nv); load(); }} />
      )}
    </div>
  );
}
