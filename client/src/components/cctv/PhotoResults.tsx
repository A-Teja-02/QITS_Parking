import { useState } from 'react';
import { AlertOctagon, CheckCircle, User, Clock, ExternalLink, ImageOff } from 'lucide-react';
import { cctvApi, VERDICT_STYLE, VIOLATION_LABEL, type PhotoCheckResult, type PhotoSlotResult } from './cctvApi';
import { ZoomImage } from './ZoomImage';

function ViolationCard({ s, onOpenViolation }: { s: PhotoSlotResult; onOpenViolation?: (id: string) => void }) {
  const severe = s.violation_type === 'WRONG_SLOT' || s.violation_type === 'UNKNOWN_VEHICLE';
  const accent = severe ? '#DC2626' : '#D97706';
  const mu = s.matched_user;
  return (
    <div style={{ background: '#fff', border: `2px solid ${accent}`, borderRadius: 12, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <div style={{ background: accent, color: '#fff', padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700 }}>
        <AlertOctagon size={18} /> {VIOLATION_LABEL[s.violation_type || ''] ?? 'Violation'} · Slot {s.slot_name || s.slot_id}
        {s.floor && <span style={{ fontWeight: 500, opacity: 0.85 }}>· {s.floor}</span>}
      </div>
      <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: '1 1 220px' }}>
            <span style={{ fontSize: 11, color: '#64748B', fontWeight: 600 }}>NUMBER PLATE CAPTURED (tap to enlarge)</span>
            {s.plate_crop_base64 ? (
              <ZoomImage src={s.plate_crop_base64} alt={`Plate ${s.detected_plate}`} caption={s.detected_plate || undefined}
                style={{ width: 320, maxWidth: '100%', borderRadius: 6, border: '2px solid #0F172A', background: '#fff' }} />
            ) : (
              <span style={{ fontSize: 12, color: '#94A3B8', display: 'flex', gap: 4, alignItems: 'center' }}><ImageOff size={14} /> no plate image</span>
            )}
            <span style={{ fontFamily: 'monospace', fontSize: 22, fontWeight: 800, color: '#0F172A', letterSpacing: 1 }}>{s.detected_plate}</span>
            <span style={{ fontSize: 11, color: '#64748B' }}>
              {Math.round((s.confidence || 0) * 100)}% confidence{s.plate_reads ? ` · ${s.plate_reads} matching read${s.plate_reads > 1 ? 's' : ''}` : ''}
              {s.plate_match === 'fuzzy' && ` · approximate match (AI read ${s.raw_plate}) – check the image`}
            </span>
          </div>
          {s.vehicle_crop_base64 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: '0 1 200px' }}>
              <span style={{ fontSize: 11, color: '#64748B', fontWeight: 600 }}>VEHICLE</span>
              <ZoomImage src={s.vehicle_crop_base64} alt="Vehicle" caption={`Slot ${s.slot_name || s.slot_id}`}
                style={{ width: 200, maxWidth: '100%', maxHeight: 150, objectFit: 'cover', borderRadius: 6, border: '1px solid #E2E8F0' }} />
            </div>
          )}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8, fontSize: 12 }}>
          <div style={{ background: mu ? '#FFF7ED' : '#F8FAFC', borderRadius: 8, padding: '8px 10px', display: 'grid', gap: 2 }}>
            <strong style={{ fontSize: 11, color: '#64748B' }}>VEHICLE OWNER</strong>
            {mu ? (
              <>
                <span style={{ fontWeight: 700, fontSize: 13, display: 'flex', alignItems: 'center', gap: 4 }}><User size={13} /> {mu.name}</span>
                {mu.employee_id && <span>ID {mu.employee_id}</span>}
                <span>{[mu.department, mu.role].filter(Boolean).join(' · ')}</span>
                {mu.email && <span>{mu.email}</span>}
                {mu.mobile_number && <span>{mu.mobile_number}</span>}
                <span style={{ fontWeight: 600 }}>{mu.slot_is_permanent ? 'Reserved slot' : 'Booking today'}: {mu.reserved_slot_today ? `slot ${mu.reserved_slot_today}` : 'none'}</span>
              </>
            ) : <span style={{ fontWeight: 600, color: '#991B1B' }}>Not a registered company vehicle</span>}
          </div>
          <div style={{ background: '#F8FAFC', borderRadius: 8, padding: '8px 10px', display: 'grid', gap: 2 }}>
            <strong style={{ fontSize: 11, color: '#64748B' }}>SLOT {s.slot_name || s.slot_id} IS FOR</strong>
            {s.expected_user ? (
              <>
                <span style={{ fontWeight: 700, fontSize: 13 }}>{s.expected_user}</span>
                <span style={{ fontFamily: 'monospace' }}>{s.expected_plate || '—'}</span>
                <span style={{ color: '#64748B' }}>{s.expected_source === 'manager_assigned' ? 'Permanently reserved slot' : 'Booked today'}</span>
              </>
            ) : <span style={{ color: '#64748B' }}>Nobody booked it today</span>}
          </div>
        </div>

        <div style={{ fontSize: 13, color: accent, fontWeight: 600 }}>{s.message}</div>
        {s.violation_id && (
          <button className="cctv-btn secondary small" style={{ alignSelf: 'flex-start' }} onClick={() => onOpenViolation?.(s.violation_id!)}>
            <ExternalLink size={13} /> {s.already_logged ? 'Already in Violations log' : 'Saved to Violations log'} – open
          </button>
        )}
      </div>
    </div>
  );
}

function UnreadableCard({ s, onResolved }: { s: PhotoSlotResult; onResolved: (r: PhotoSlotResult) => void }) {
  const [plate, setPlate] = useState(s.suggested_plate || '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true); setErr(null);
    try { const r = await cctvApi.classifyPlate(s.slot_id, plate); onResolved({ ...s, ...r, confidence: 1, plate_reads: 0, raw_plate: plate } as PhotoSlotResult); }
    catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <div style={{ background: '#fff', border: '2px solid #EAB308', borderRadius: 12, padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <strong style={{ color: '#92400E' }}>Slot {s.slot_name || s.slot_id}: vehicle parked - {s.suggested_plate ? 'please confirm the number plate' : 'plate not readable by AI'}</strong>
      <span style={{ fontSize: 12, color: '#78350F' }}>{s.unreadable_reason}</span>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        {s.plate_crop_base64 && <div><div style={{ fontSize: 11, color: '#64748B' }}>PLATE AREA (tap to enlarge)</div>
          <ZoomImage src={s.plate_crop_base64} alt="Plate area" style={{ width: 260, maxWidth: '100%', borderRadius: 6, border: '1px solid #CBD5E1' }} /></div>}
        {s.vehicle_crop_base64 && <div><div style={{ fontSize: 11, color: '#64748B' }}>VEHICLE (tap to enlarge)</div>
          <ZoomImage src={s.vehicle_crop_base64} alt="Vehicle" style={{ width: 200, maxWidth: '100%', maxHeight: 150, objectFit: 'cover', borderRadius: 6 }} /></div>}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <input className="cctv-input" value={plate} onChange={e => setPlate(e.target.value.toUpperCase())} placeholder="Type plate, e.g. TS 09 EA 9999" title={s.suggested_plate ? `AI guess: ${s.suggested_plate} - correct it if needed` : undefined} style={{ fontFamily: 'monospace', flex: '1 1 200px' }} />
        <button className="cctv-btn primary small" disabled={busy || plate.replace(/\s/g, '').length < 6} onClick={go}>{s.suggested_plate ? 'Confirm & check' : 'Check this plate'}</button>
      </div>
      {err && <span style={{ color: '#B91C1C', fontSize: 12 }}>{err}</span>}
    </div>
  );
}

export function PhotoResults({ result, onOpenViolation }: { result: PhotoCheckResult; onOpenViolation?: (id: string) => void }) {
  const [manual, setManual] = useState<Record<string, PhotoSlotResult>>({});
  const slots = result.slots.map(s => manual[s.slot_id] ?? s);
  const violations = slots.filter(s => s.violation_type);
  const unreadable = slots.filter(s => s.verification_status === 'OCCUPIED_NO_PLATE');
  const others = slots.filter(s => !s.violation_type && s.verification_status !== 'OCCUPIED_NO_PLATE');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 8, background: '#fff', border: '1px solid #E2E8F0', borderRadius: 10, padding: 10, textAlign: 'center' }}>
        {[
          ['Slots checked', result.total_slots, '#1E3A5F'],
          ['Occupied', result.occupied_count, '#B45309'],
          ['Vacant', result.vacant_count, '#047857'],
          ['Violations', violations.length, '#DC2626'],
        ].map(([l, v, c]) => (
          <div key={l as string}>
            <div style={{ fontSize: 24, fontWeight: 800, color: c as string }}>{v as number}</div>
            <div style={{ fontSize: 11, color: '#64748B', fontWeight: 600 }}>{l as string}</div>
          </div>
        ))}
        <div>
          <div style={{ fontSize: 16, fontWeight: 700, color: '#475569', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 4, marginTop: 4 }}>
            <Clock size={14} /> {(result.elapsed_ms / 1000).toFixed(1)}s
          </div>
          <div style={{ fontSize: 11, color: '#64748B', fontWeight: 600 }}>AI time</div>
        </div>
      </div>

      {violations.length > 0 ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 420px), 1fr))', gap: 12 }}>
          {violations.map(s => <ViolationCard key={s.slot_id} s={s} onOpenViolation={onOpenViolation} />)}
        </div>
      ) : unreadable.length ? null : (
        <div className="cctv-test-result success"><CheckCircle size={16} /><span>No violations – every parked vehicle is in its own booked slot.</span></div>
      )}

      {unreadable.map(s => <UnreadableCard key={s.slot_id} s={s} onResolved={r => setManual(m => ({ ...m, [s.slot_id]: r }))} />)}

      {others.length > 0 && (
        <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 10, padding: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <strong style={{ fontSize: 13 }}>Other slots</strong>
          {others.map(s => {
            const vs = VERDICT_STYLE[s.verification_status] ?? VERDICT_STYLE.DETECTING;
            return (
              <div key={s.slot_id} style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '6px 8px', borderRadius: 8, background: '#F8FAFC' }}>
                <span style={{ fontWeight: 800, minWidth: 56 }}>{s.slot_name || s.slot_id}</span>
                <span style={{ fontSize: 11, fontWeight: 700, color: vs.color, background: vs.bg, padding: '3px 8px', borderRadius: 6 }}>{vs.label}</span>
                {s.plate_crop_base64 && (
                  <ZoomImage src={s.plate_crop_base64} alt="plate" caption={s.detected_plate || undefined}
                    style={{ height: 30, maxWidth: 130, objectFit: 'contain', borderRadius: 4, border: '1px solid #CBD5E1', background: '#fff' }} />
                )}
                {s.detected_plate && <span style={{ fontFamily: 'monospace', fontWeight: 700 }}>{s.detected_plate}</span>}
                <span style={{ fontSize: 12, color: '#475569', flex: 1, minWidth: 180 }}>{s.message}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
