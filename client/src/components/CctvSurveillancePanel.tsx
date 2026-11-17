import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, CheckCircle, AlertTriangle, MonitorPlay, AlertOctagon, LayoutGrid, Video, FlaskConical } from 'lucide-react';
import { cctvApi, type Camera } from './cctv/cctvApi';
import { LiveMonitor } from './cctv/LiveMonitor';
import { CameraManager } from './cctv/CameraManager';
import { SlotLayoutEditor } from './cctv/SlotLayoutEditor';
import { ViolationsLog } from './cctv/ViolationsLog';

type Tab = 'live' | 'violations' | 'layout' | 'cameras' | 'test';

/**
 * Admin CCTV & AI panel.
 * Flow: add an RTSP camera → draw its slot layout → the server monitors those slots
 * continuously (occupancy + number plate) → wrong vehicles become violations with photos.
 */
export function CctvSurveillancePanel() {
  const [tab, setTab] = useState<Tab>('live');
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [aiStatus, setAiStatus] = useState<'checking' | 'online' | 'offline'>('checking');
  const [focusCam, setFocusCam] = useState<string | null>(null);
  const [focusViolation, setFocusViolation] = useState<string | null>(null);
  const [openViolations, setOpenViolations] = useState(0);

  const loadCameras = useCallback(async () => {
    try {
      setCameras(await cctvApi.cameras());
      setAiStatus('online');
    } catch {
      setAiStatus(await cctvApi.health() ? 'online' : 'offline');
    }
  }, []);

  useEffect(() => {
    loadCameras();
    const t = setInterval(loadCameras, 5000);
    return () => clearInterval(t);
  }, [loadCameras]);

  // Keep the open-violation badge fresh even when the Violations tab is closed
  useEffect(() => {
    const tick = () => cctvApi.violations({ status: 'open', limit: 1 }).then(r => setOpenViolations(r.status_counts.open || 0)).catch(() => {});
    tick();
    const t = setInterval(tick, 15000);
    return () => clearInterval(t);
  }, []);

  // Start on the Cameras tab when nothing is configured yet
  useEffect(() => {
    if (aiStatus === 'online' && cameras.length === 0 && tab === 'live') setTab('cameras');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aiStatus]);

  const openLayout = (id: string) => { setFocusCam(id); setTab('layout'); };
  const openLive = (id: string) => { setFocusCam(id); setTab('live'); };
  const openViolation = (id: string) => { setFocusViolation(id); setTab('violations'); };

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: 'live', label: 'Live monitor', icon: <MonitorPlay size={15} /> },
    { id: 'violations', label: `Violations${openViolations ? ` (${openViolations})` : ''}`, icon: <AlertOctagon size={15} /> },
    { id: 'layout', label: 'Slot layout', icon: <LayoutGrid size={15} /> },
    { id: 'cameras', label: `Cameras (${cameras.length})`, icon: <Video size={15} /> },
    { id: 'test', label: 'Photo test', icon: <FlaskConical size={15} /> },
  ];

  return (
    <div className="cctv-panel">
      <div className={`cctv-ai-status ${aiStatus}`}>
        {aiStatus === 'checking' && <><RefreshCw size={14} className="spin" /> Checking AI engine…</>}
        {aiStatus === 'online' && (
          <><CheckCircle size={14} /> AI engine online · {cameras.filter(c => c.status === 'connected').length}/{cameras.length} cameras live ·{' '}
            {cameras.reduce((n, c) => n + (c.status === 'connected' ? c.monitored_slots?.length || 0 : 0), 0)} slots monitored</>
        )}
        {aiStatus === 'offline' && <><AlertTriangle size={14} /> AI backend offline – start it on port 8001 (run_all.bat)</>}
      </div>

      <div className="cctv-subtabs">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} className={`cctv-subtab ${tab === t.id ? 'active' : ''}`}
            style={t.id === 'violations' && openViolations ? { color: tab === t.id ? undefined : '#B91C1C' } : undefined}>
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {tab === 'live' && <LiveMonitor cameras={cameras} initialCameraId={focusCam} onOpenLayout={openLayout} onOpenViolation={openViolation} />}
      {tab === 'violations' && <ViolationsLog focusId={focusViolation} onCountChange={setOpenViolations} />}
      {tab === 'layout' && <SlotLayoutEditor cameras={cameras} initialCameraId={focusCam} onSaved={loadCameras} />}
      {tab === 'cameras' && <CameraManager cameras={cameras} reload={loadCameras} onOpenLayout={openLayout} onOpenLive={openLive} />}
      {tab === 'test' && <SlotLayoutEditor mode="photo" cameras={cameras} onSaved={loadCameras} onOpenViolation={openViolation} />}
    </div>
  );
}
