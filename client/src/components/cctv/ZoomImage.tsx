import { useEffect, useState, type CSSProperties } from 'react';
import { X, ZoomIn } from 'lucide-react';

/** Thumbnail that opens full-screen when tapped (plate crops, vehicle photos, evidence). */
export function ZoomImage({ src, alt, caption, style, hint = true }: {
  src: string;
  alt: string;
  caption?: string;
  style?: CSSProperties;
  hint?: boolean;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <>
      <span style={{ position: 'relative', display: 'inline-block', lineHeight: 0, cursor: 'zoom-in', maxWidth: '100%', alignSelf: 'flex-start' }}
        onClick={e => { e.stopPropagation(); setOpen(true); }} title="Tap to enlarge">
        <img src={src} alt={alt} style={style} />
        {hint && (
          <span style={{ position: 'absolute', right: 3, bottom: 3, background: 'rgba(15,23,42,.7)', color: '#fff', borderRadius: 4, padding: 2, lineHeight: 0 }}>
            <ZoomIn size={12} />
          </span>
        )}
      </span>
      {open && (
        <div onClick={e => { e.stopPropagation(); setOpen(false); }}
          style={{ position: 'fixed', inset: 0, zIndex: 3000, background: 'rgba(2,6,23,.88)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 16, cursor: 'zoom-out' }}>
          <button onClick={() => setOpen(false)} aria-label="Close"
            style={{ position: 'absolute', top: 14, right: 14, background: 'rgba(255,255,255,.15)', border: 'none', color: '#fff', borderRadius: 8, padding: 8, cursor: 'pointer' }}>
            <X size={22} />
          </button>
          <img src={src} alt={alt}
            style={{ maxWidth: '96vw', maxHeight: caption ? '82vh' : '90vh', minWidth: 'min(90vw, 640px)', objectFit: 'contain', borderRadius: 8, background: '#fff', imageRendering: 'auto' }} />
          {caption && <div style={{ color: '#fff', marginTop: 12, fontSize: 18, fontWeight: 700, fontFamily: 'monospace', textAlign: 'center' }}>{caption}</div>}
        </div>
      )}
    </>
  );
}
