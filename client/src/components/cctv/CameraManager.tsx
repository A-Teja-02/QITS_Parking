import { useState } from 'react';
import {
  Plus, Trash2, Play, Square, RefreshCw, Wifi, WifiOff, Video, X, CheckCircle, AlertTriangle,
  Pencil, LayoutGrid, Eye, ChevronDown, ChevronUp,
} from 'lucide-react';
import { cctvApi, type Camera, type CameraInput, type TestResult, type OnvifDevice, type OnvifStream } from './cctvApi';

type Mode = 'onvif' | 'url' | 'brand' | 'usb' | 'file';

const BRANDS = [
  { id: 'hikvision', label: 'Hikvision' },
  { id: 'dahua', label: 'Dahua' },
  { id: 'cpplus', label: 'CP Plus' },
  { id: 'uniview', label: 'Uniview' },
  { id: 'reolink', label: 'Reolink' },
  { id: 'tapo', label: 'TP-Link Tapo' },
  { id: 'axis', label: 'Axis' },
  { id: 'generic', label: 'Other (custom path)' },
];

function modeOf(c?: Camera | null): Mode {
  if (!c) return 'url';
  if (c.source_type === 'usb') return 'usb';
  if (c.source_type === 'file') return 'file';
  if (!c.rtsp_url && c.host) return 'brand';
  return 'url';
}

function CameraForm({ initial, onSaved, onCancel }: { initial?: Camera | null; onSaved: () => void; onCancel: () => void }) {
  const [mode, setMode] = useState<Mode>(initial ? modeOf(initial) : 'onvif');
  const [onvifDevices, setOnvifDevices] = useState<OnvifDevice[] | null>(null);
  const [onvifHost, setOnvifHost] = useState(initial?.host ?? '');
  const [onvifPort, setOnvifPort] = useState(80);
  const [onvifUser, setOnvifUser] = useState('admin');
  const [onvifPass, setOnvifPass] = useState('');
  const [onvifStreams, setOnvifStreams] = useState<OnvifStream[] | null>(null);
  const [onvifInfo, setOnvifInfo] = useState<string | null>(null);
  const [onvifBusy, setOnvifBusy] = useState<string | null>(null);
  const [f, setF] = useState<CameraInput>(() => ({
    name: initial?.name ?? '',
    location: initial?.location ?? '',
    rtsp_url: initial?.rtsp_url ?? '',
    brand: initial?.brand ?? 'hikvision',
    host: initial?.host ?? '',
    port: initial?.port ?? 554,
    username: initial?.username ?? 'admin',
    password: initial?.password ?? '',
    channel: initial?.channel ?? 1,
    stream_path: initial?.stream_path ?? '',
    transport: initial?.transport ?? 'tcp',
    stream_profile: initial?.stream_profile ?? 'main',
    analysis_interval: initial?.analysis_interval ?? 1.5,
    violation_dwell_seconds: initial?.violation_dwell_seconds ?? 20,
    min_confidence: initial?.min_confidence ?? 0.35,
    detection_imgsz: initial?.detection_imgsz ?? 0,
    enable_plate_recognition: initial?.enable_plate_recognition ?? true,
    flag_unreadable_plates: initial?.flag_unreadable_plates ?? false,
    monitoring_enabled: initial?.monitoring_enabled ?? true,
  }));
  const [advanced, setAdvanced] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => setF(p => ({ ...p, [k]: v }));

  const payload = (): CameraInput => {
    const base: CameraInput = { ...f, name: f.name.trim() };
    if (mode === 'url' || mode === 'onvif') return { ...base, source_type: 'rtsp', host: mode === 'onvif' ? onvifHost || null : null };
    if (mode === 'brand') return { ...base, source_type: 'rtsp', rtsp_url: null };
    if (mode === 'usb') return { ...base, source_type: 'usb', rtsp_url: String(f.rtsp_url || '0'), host: null };
    return { ...base, source_type: 'file', host: null };
  };

  const canConnect = mode === 'usb' || (mode === 'brand' ? !!f.host : !!f.rtsp_url);

  const findOnvif = async () => {
    setOnvifBusy('discover'); setError(null);
    try { setOnvifDevices((await cctvApi.onvifDiscover()).devices); } catch (e: any) { setError(e.message); }
    setOnvifBusy(null);
  };
  const getOnvifStreams = async () => {
    setOnvifBusy('streams'); setError(null); setOnvifStreams(null);
    try {
      const r = await cctvApi.onvifStreams(onvifHost.trim(), onvifPort, onvifUser, onvifPass);
      setOnvifStreams(r.streams);
      const d = r.device || {};
      const model = [d.Manufacturer, d.Model].filter(Boolean).join(' ');
      setOnvifInfo(model || null);
      if (r.streams[0]) {
        set('rtsp_url', r.streams[0].rtsp_url);
        if (!f.name.trim()) set('name', model || `Camera ${onvifHost}`);
      }
    } catch (e: any) { setError(e.message); }
    setOnvifBusy(null);
  };

  const runTest = async () => {
    setTesting(true); setTest(null); setError(null);
    try { setTest(await cctvApi.testCamera({ ...payload(), camera_id: initial?.id } as Partial<CameraInput>)); }
    catch (e: any) { setError(e.message); }
    setTesting(false);
  };

  const save = async () => {
    if (!f.name.trim()) { setError('Give the camera a name'); return; }
    setSaving(true); setError(null);
    try {
      if (initial) await cctvApi.updateCamera(initial.id, payload());
      else await cctvApi.addCamera(payload());
      onSaved();
    } catch (e: any) { setError(e.message); }
    setSaving(false);
  };

  const num = (v: string) => (v === '' ? undefined : Number(v));

  return (
    <div className="cctv-wizard">
      <div className="cctv-wizard-header">
        <h3>{initial ? `Edit ${initial.name}` : 'Add CCTV camera'}</h3>
        <button onClick={onCancel} className="cctv-icon-btn" title="Close"><X size={18} /></button>
      </div>

      <div className="cctv-form-grid">
        <div className="cctv-form-group">
          <label>Camera name</label>
          <input className="cctv-input" value={f.name} onChange={e => set('name', e.target.value)} placeholder="e.g. B1 – Row A (P101–P104)" />
        </div>
        <div className="cctv-form-group">
          <label>Location (optional)</label>
          <input className="cctv-input" value={f.location ?? ''} onChange={e => set('location', e.target.value)} placeholder="e.g. Basement 1, pillar 4" />
        </div>

        <div className="cctv-form-group full-width">
          <label>How to connect</label>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {([['onvif', 'ONVIF (find automatically)'], ['url', 'RTSP / stream URL'], ['brand', 'Camera IP + login'], ['usb', 'USB webcam'], ['file', 'Video file (testing)']] as [Mode, string][]).map(([m, l]) => (
              <button key={m} type="button" onClick={() => { setMode(m); setTest(null); }}
                className={`cctv-preset-btn ${mode === m ? 'active' : ''}`}
                style={mode === m ? { background: '#1E3A5F', color: '#fff' } : undefined}>{l}</button>
            ))}
          </div>
        </div>

        {mode === 'onvif' && (
          <div className="cctv-form-group full-width" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <button type="button" className="cctv-btn secondary small" onClick={findOnvif} disabled={onvifBusy === 'discover'}>
                {onvifBusy === 'discover' ? <RefreshCw size={14} className="spin" /> : <Wifi size={14} />} Find cameras on network
              </button>
              <small style={{ color: '#64748B' }}>The AI server must be on the same network as the cameras / NVR.</small>
            </div>
            {onvifDevices && (
              onvifDevices.length === 0 ? (
                <small style={{ color: '#B45309' }}>No ONVIF devices answered. Enable ONVIF on the camera/NVR, or type its IP below.</small>
              ) : (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {onvifDevices.map(d => (
                    <button key={d.ip} type="button" onClick={() => { setOnvifHost(d.ip); setOnvifPort(d.port || 80); setOnvifStreams(null); }}
                      className={`cctv-preset-btn ${onvifHost === d.ip ? 'active' : ''}`}
                      style={onvifHost === d.ip ? { background: '#1E3A5F', color: '#fff' } : undefined}>
                      {d.ip}{d.name || d.hardware ? ` · ${[d.name, d.hardware].filter(Boolean).join(' ')}` : ''}
                    </button>
                  ))}
                </div>
              )
            )}
            <div className="cctv-form-grid">
              <div className="cctv-form-group"><label>Camera / NVR IP</label>
                <input className="cctv-input" value={onvifHost} onChange={e => setOnvifHost(e.target.value)} placeholder="192.168.1.64" /></div>
              <div className="cctv-form-group"><label>ONVIF port</label>
                <input className="cctv-input" type="number" value={onvifPort} onChange={e => setOnvifPort(Number(e.target.value) || 80)} /></div>
              <div className="cctv-form-group"><label>ONVIF username</label>
                <input className="cctv-input" value={onvifUser} onChange={e => setOnvifUser(e.target.value)} autoComplete="off" /></div>
              <div className="cctv-form-group"><label>ONVIF password</label>
                <input className="cctv-input" type="password" value={onvifPass} onChange={e => setOnvifPass(e.target.value)} autoComplete="new-password" /></div>
            </div>
            <button type="button" className="cctv-btn primary small" style={{ alignSelf: 'flex-start' }} onClick={getOnvifStreams} disabled={!onvifHost.trim() || onvifBusy === 'streams'}>
              {onvifBusy === 'streams' ? <RefreshCw size={14} className="spin" /> : <Video size={14} />} Get streams
            </button>
            {onvifStreams && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {onvifInfo && <small style={{ color: '#047857', fontWeight: 600 }}>Connected: {onvifInfo}</small>}
                {onvifStreams.map(s => (
                  <label key={s.token} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, cursor: 'pointer', flexWrap: 'wrap' }}>
                    <input type="radio" name="onvif-stream" checked={f.rtsp_url === s.rtsp_url} onChange={() => set('rtsp_url', s.rtsp_url)} />
                    <strong>{s.name}</strong> {s.width}x{s.height}{s.encoding ? ` ${s.encoding}` : ''}
                    <code style={{ color: '#64748B', wordBreak: 'break-all' }}>{s.display_url}</code>
                  </label>
                ))}
                <small style={{ color: '#64748B' }}>Use the highest resolution (main) stream for reliable number plates.</small>
              </div>
            )}
          </div>
        )}

        {mode === 'url' && (
          <div className="cctv-form-group full-width">
            <label>Stream URL</label>
            <input className="cctv-input" value={f.rtsp_url ?? ''} onChange={e => set('rtsp_url', e.target.value)}
              placeholder="rtsp://admin:password@192.168.1.64:554/Streaming/Channels/101" />
            <small style={{ color: '#64748B', fontSize: 11, lineHeight: 1.5 }}>
              Hikvision: <code>rtsp://user:pass@IP:554/Streaming/Channels/101</code> · Dahua / CP Plus: <code>rtsp://user:pass@IP:554/cam/realmonitor?channel=1&amp;subtype=0</code> · NVR channel 3 on Hikvision: <code>/Streaming/Channels/301</code>
            </small>
          </div>
        )}

        {mode === 'brand' && (
          <>
            <div className="cctv-form-group">
              <label>Brand</label>
              <select className="cctv-input" value={f.brand ?? 'generic'} onChange={e => set('brand', e.target.value)}>
                {BRANDS.map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
              </select>
            </div>
            <div className="cctv-form-group">
              <label>Camera / NVR IP address</label>
              <input className="cctv-input" value={f.host ?? ''} onChange={e => set('host', e.target.value)} placeholder="192.168.1.64" />
            </div>
            <div className="cctv-form-group">
              <label>Username</label>
              <input className="cctv-input" value={f.username ?? ''} onChange={e => set('username', e.target.value)} autoComplete="off" />
            </div>
            <div className="cctv-form-group">
              <label>Password</label>
              <input className="cctv-input" type="password" value={f.password ?? ''} onChange={e => set('password', e.target.value)} autoComplete="new-password" />
            </div>
            <div className="cctv-form-group">
              <label>RTSP port</label>
              <input className="cctv-input" type="number" value={f.port ?? 554} onChange={e => set('port', num(e.target.value))} />
            </div>
            <div className="cctv-form-group">
              <label>Channel (NVR camera number)</label>
              <input className="cctv-input" type="number" min={1} value={f.channel ?? 1} onChange={e => set('channel', num(e.target.value))} />
            </div>
            <div className="cctv-form-group">
              <label>Stream quality</label>
              <select className="cctv-input" value={f.stream_profile ?? 'main'} onChange={e => set('stream_profile', e.target.value)}>
                <option value="main">Main stream (best for number plates)</option>
                <option value="sub">Sub stream (lower bandwidth)</option>
              </select>
            </div>
            {f.brand === 'generic' && (
              <div className="cctv-form-group">
                <label>Stream path</label>
                <input className="cctv-input" value={f.stream_path ?? ''} onChange={e => set('stream_path', e.target.value)} placeholder="live/ch0" />
              </div>
            )}
          </>
        )}

        {mode === 'usb' && (
          <div className="cctv-form-group">
            <label>Device index (on the AI server)</label>
            <input className="cctv-input" value={f.rtsp_url || '0'} onChange={e => set('rtsp_url', e.target.value)} placeholder="0" />
          </div>
        )}

        {mode === 'file' && (
          <div className="cctv-form-group full-width">
            <label>Video file path (on the AI server) – loops like a live camera</label>
            <input className="cctv-input" value={f.rtsp_url ?? ''} onChange={e => set('rtsp_url', e.target.value)} placeholder="C:\\recordings\\basement1.mp4" />
          </div>
        )}

        {(mode === 'url' || mode === 'brand' || mode === 'onvif') && (
          <div className="cctv-form-group">
            <label>Transport</label>
            <select className="cctv-input" value={f.transport ?? 'tcp'} onChange={e => set('transport', e.target.value)}>
              <option value="tcp">TCP (recommended – no smeared frames)</option>
              <option value="udp">UDP</option>
            </select>
          </div>
        )}
      </div>

      <button type="button" onClick={() => setAdvanced(a => !a)} className="cctv-btn secondary small" style={{ alignSelf: 'flex-start' }}>
        {advanced ? <ChevronUp size={14} /> : <ChevronDown size={14} />} Detection settings
      </button>
      {advanced && (
        <div className="cctv-form-grid" style={{ background: '#F8FAFC', padding: 12, borderRadius: 8 }}>
          <div className="cctv-form-group">
            <label>Analyse every (seconds)</label>
            <input className="cctv-input" type="number" step={0.5} min={0.5} value={f.analysis_interval ?? 1.5} onChange={e => set('analysis_interval', num(e.target.value))} />
          </div>
          <div className="cctv-form-group">
            <label>Car must stay parked before violation (seconds)</label>
            <input className="cctv-input" type="number" min={0} value={f.violation_dwell_seconds ?? 20} onChange={e => set('violation_dwell_seconds', num(e.target.value))} />
          </div>
          <div className="cctv-form-group">
            <label>Vehicle detection confidence</label>
            <input className="cctv-input" type="number" step={0.05} min={0.1} max={0.9} value={f.min_confidence ?? 0.35} onChange={e => set('min_confidence', num(e.target.value))} />
          </div>
          <div className="cctv-form-group">
            <label>Detection resolution</label>
            <select className="cctv-input" value={f.detection_imgsz ?? 0} onChange={e => set('detection_imgsz', Number(e.target.value))}>
              <option value={0}>Auto (1280 for HD cameras)</option>
              <option value={640}>640 – fastest</option>
              <option value={960}>960</option>
              <option value={1280}>1280 – best for far slots</option>
            </select>
          </div>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            <input type="checkbox" checked={!!f.monitoring_enabled} onChange={e => set('monitoring_enabled', e.target.checked)} /> Monitor slots continuously
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            <input type="checkbox" checked={!!f.enable_plate_recognition} onChange={e => set('enable_plate_recognition', e.target.checked)} /> Read number plates
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            <input type="checkbox" checked={!!f.flag_unreadable_plates} onChange={e => set('flag_unreadable_plates', e.target.checked)} /> Log a review item when a parked car's plate can't be read for 2 min
          </label>
        </div>
      )}

      {error && <div className="cctv-test-result error"><AlertTriangle size={16} /><span>{error}</span></div>}
      {test && (
        <div className={`cctv-test-result ${test.connected ? 'success' : 'error'}`} style={{ flexDirection: 'column', alignItems: 'stretch' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {test.connected ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
            <span>{test.message}{test.connected && ` · ${test.resolution} · ${test.latency_ms} ms`}</span>
          </div>
          {test.stream_url && <code style={{ fontSize: 11, opacity: 0.8, wordBreak: 'break-all' }}>{test.stream_url}</code>}
          {test.preview_base64 && <img src={test.preview_base64} alt="Camera preview" style={{ marginTop: 8, maxWidth: 420, width: '100%', borderRadius: 6 }} />}
        </div>
      )}

      <div className="cctv-wizard-actions">
        <button onClick={runTest} disabled={!canConnect || testing} className="cctv-btn secondary">
          {testing ? <RefreshCw size={14} className="spin" /> : <Wifi size={14} />} Test connection
        </button>
        <button onClick={save} disabled={!f.name.trim() || !canConnect || saving} className="cctv-btn primary">
          {saving ? <RefreshCw size={14} className="spin" /> : <CheckCircle size={14} />} {initial ? 'Save changes' : 'Add & start camera'}
        </button>
      </div>
    </div>
  );
}

function statusBadge(c: Camera) {
  const map: Record<string, [string, string, string]> = {
    connected: ['Live', '#047857', '#ECFDF5'],
    connecting: ['Connecting', '#B45309', '#FFFBEB'],
    reconnecting: ['Reconnecting', '#B45309', '#FFFBEB'],
    error: ['Error', '#B91C1C', '#FEF2F2'],
    stopped: ['Stopped', '#475569', '#F1F5F9'],
  };
  const [label, color, bg] = map[c.status] ?? [c.status, '#475569', '#F1F5F9'];
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 700, color, background: bg, padding: '3px 8px', borderRadius: 6 }}>
      {c.status === 'connected' ? <Wifi size={12} /> : <WifiOff size={12} />} {label}
    </span>
  );
}

export function CameraManager({ cameras, reload, onOpenLayout, onOpenLive }: {
  cameras: Camera[];
  reload: () => void;
  onOpenLayout: (id: string) => void;
  onOpenLive: (id: string) => void;
}) {
  const [editing, setEditing] = useState<Camera | null | 'new'>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const act = async (id: string, fn: () => Promise<unknown>) => {
    setBusy(id); setErr(null);
    try { await fn(); } catch (e: any) { setErr(e.message); }
    setBusy(null);
    setTimeout(reload, 800);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="cctv-section-header">
        <h3>Cameras ({cameras.length})</h3>
        {editing === null && (
          <button onClick={() => setEditing('new')} className="cctv-btn primary small"><Plus size={14} /> Add camera</button>
        )}
      </div>

      {editing !== null && (
        <CameraForm
          key={editing === 'new' ? 'new' : editing.id}
          initial={editing === 'new' ? null : editing}
          onSaved={() => { setEditing(null); reload(); }}
          onCancel={() => setEditing(null)}
        />
      )}

      {err && <div className="cctv-test-result error"><AlertTriangle size={16} /><span>{err}</span></div>}

      {cameras.length === 0 && editing === null ? (
        <div className="cctv-empty-state">
          <Video size={36} />
          <p>No cameras yet</p>
          <span>Add your RTSP / IP camera, then draw its slot layout so it can monitor those slots.</span>
        </div>
      ) : (
        <div className="cctv-cameras-grid">
          {cameras.map(c => {
            const running = c.status !== 'stopped';
            return (
              <div key={c.id} className="cctv-camera-card">
                <div className="cctv-camera-card-header">
                  <div className="cctv-camera-info">
                    <Video size={18} className="cctv-cam-icon" />
                    <div>
                      <h4>{c.name}</h4>
                      <span className="cctv-camera-type">{c.location || c.source_type.toUpperCase()}</span>
                    </div>
                  </div>
                  {statusBadge(c)}
                </div>
                <div style={{ fontSize: 12, color: '#475569', display: 'grid', gap: 3 }}>
                  <span><strong>{c.monitored_slots?.length || 0}</strong> slot{(c.monitored_slots?.length || 0) === 1 ? '' : 's'} in layout
                    {c.monitored_slots?.length ? ` (${c.monitored_slots.map(s => s.slot_id).join(', ')})` : ' – draw a layout to start monitoring'}</span>
                  {c.status === 'connected' && <span>{c.resolution} · {c.fps} fps · AI monitor {c.monitoring ? 'running' : 'off'}</span>}
                  {c.last_error && c.status !== 'connected' && <span style={{ color: '#B91C1C' }}>{c.last_error}</span>}
                  <code style={{ fontSize: 10, color: '#94A3B8', wordBreak: 'break-all' }}>{c.stream_source}</code>
                </div>
                <div className="cctv-camera-card-actions" style={{ flexWrap: 'wrap' }}>
                  <button className="cctv-btn primary small" onClick={() => onOpenLive(c.id)} disabled={!running}><Eye size={14} /> Live</button>
                  <button className="cctv-btn secondary small" onClick={() => onOpenLayout(c.id)}><LayoutGrid size={14} /> Slot layout</button>
                  <button className="cctv-btn secondary small" onClick={() => setEditing(c)}><Pencil size={14} /> Edit</button>
                  {running ? (
                    <button className="cctv-btn secondary small" disabled={busy === c.id} onClick={() => act(c.id, () => cctvApi.stopCamera(c.id))}><Square size={14} /> Stop</button>
                  ) : (
                    <button className="cctv-btn secondary small" disabled={busy === c.id} onClick={() => act(c.id, () => cctvApi.startCamera(c.id))}><Play size={14} /> Start</button>
                  )}
                  <button className="cctv-btn danger small" disabled={busy === c.id} title="Delete camera"
                    onClick={() => { if (window.confirm(`Delete camera "${c.name}" and its slot layout?`)) act(c.id, () => cctvApi.deleteCamera(c.id)); }}>
                    {busy === c.id ? <RefreshCw size={14} className="spin" /> : <Trash2 size={14} />}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
