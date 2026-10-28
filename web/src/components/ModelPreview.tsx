import { useEffect, useRef, useState } from 'react';

declare module 'react' {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace JSX { interface IntrinsicElements { 'model-viewer': React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & Record<string, unknown> } }
}
type Viewer = HTMLElement & { getDimensions(): { x: number; y: number; z: number } };

// Interactive preview of a job's GLB. model-viewer is loaded only when a mesh exists. The scaler writes
// its output Z-up (base at Z=0, tip toward +Z), so it is pitched upright for glTF's Y-up viewer.
export function ModelPreview({ src, zUp, label }: { src: string; zUp: boolean; label: string }) {
  const ref = useRef<Viewer>(null);
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState<'loading' | 'loaded' | 'error'>('loading');
  const [size, setSize] = useState<number[] | null>(null);
  useEffect(() => { let live = true; import('@google/model-viewer').then(() => live && setReady(true), () => live && setStatus('error')); return () => { live = false; }; }, []);
  useEffect(() => {
    const el = ref.current; if (!ready || !el) return;
    setStatus('loading'); setSize(null);
    const load = () => { const d = el.getDimensions(); setSize([d.x, d.y, d.z].map(v => v * 1000)); setStatus('loaded'); };
    const fail = () => setStatus('error');
    el.addEventListener('load', load); el.addEventListener('error', fail);
    return () => { el.removeEventListener('load', load); el.removeEventListener('error', fail); };
  }, [ready, src]);
  return <figure className="model-preview">
    {ready && <model-viewer ref={ref} src={src} alt={label} camera-controls="" touch-action="pan-y" shadow-intensity="0.7" exposure="1" orientation={zUp ? '0deg -90deg 0deg' : undefined}/>}
    {status !== 'loaded' && <p className="model-status">{status === 'error' ? 'The mesh could not be shown. Download it to inspect it.' : 'Loading 3D preview…'}</p>}
    <figcaption>{label}{size && <> · bounding box {size.map(v => v.toFixed(1)).join(' × ')} mm</>} · drag to rotate, scroll to zoom</figcaption>
  </figure>;
}
