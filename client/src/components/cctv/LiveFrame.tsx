import { useEffect, useRef, useState } from 'react';
import { Camera as CameraIcon } from 'lucide-react';
import { snapshotUrl } from './cctvApi';

/**
 * Live camera view built from fast JPEG snapshots (about 6-8 fps).
 * MJPEG inside an <img> does not render reliably (Chrome, Android WebView, HTTPS
 * tunnels/proxies), while plain JPEGs work everywhere. The next frame is only
 * requested after the previous one arrived, so slow networks never pile up.
 */
export function LiveFrame({ cameraId, annotated = true, alt, maxWidth = 1280, intervalMs = 120 }: {
  cameraId: string;
  annotated?: boolean;
  alt: string;
  maxWidth?: number;
  intervalMs?: number;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [failures, setFailures] = useState(0);
  const lastUrl = useRef<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const next = () => {
      if (!alive) return;
      if (document.hidden) { timer = window.setTimeout(next, 1000); return; } // pause in background tabs
      const img = new Image();
      img.onload = () => {
        if (!alive) return;
        setSrc(img.src);
        lastUrl.current = img.src;
        setFailures(0);
        timer = window.setTimeout(next, intervalMs);
      };
      img.onerror = () => {
        if (!alive) return;
        setFailures(f => f + 1);
        timer = window.setTimeout(next, 1500);
      };
      img.src = snapshotUrl(cameraId, annotated, maxWidth);
    };
    setSrc(null);
    next();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [cameraId, annotated, maxWidth, intervalMs]);

  if (!src) {
    return (
      <div style={{ color: '#94A3B8', textAlign: 'center', padding: 40 }}>
        <CameraIcon size={40} style={{ opacity: 0.5 }} />
        <p>{failures > 2 ? 'Waiting for the camera picture…' : 'Connecting to camera…'}</p>
      </div>
    );
  }
  return (
    <div style={{ position: 'relative', width: '100%' }}>
      <img src={src} alt={alt} style={{ width: '100%', display: 'block' }} />
      {failures > 3 && (
        <span style={{ position: 'absolute', top: 8, left: 8, background: 'rgba(185,28,28,.9)', color: '#fff', fontSize: 11, fontWeight: 700, padding: '3px 8px', borderRadius: 6 }}>
          Picture paused – reconnecting
        </span>
      )}
      {failures === 0 && (
        <span style={{ position: 'absolute', top: 8, left: 8, background: 'rgba(220,38,38,.9)', color: '#fff', fontSize: 11, fontWeight: 700, padding: '3px 8px', borderRadius: 6 }}>
          ● LIVE
        </span>
      )}
    </div>
  );
}
