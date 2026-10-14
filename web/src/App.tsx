import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, Download, Move, Ruler, Upload, Box, ShieldCheck } from 'lucide-react';
import { Badge, Button, buttonVariants, Card, CardContent, CardDescription, CardHeader, CardTitle } from './components/ui';
import { cn } from './lib/utils';
import { fractions, landmarkDistance, station, validMm, type Point } from './capture';

type Photo = { file: File; url: string; width: number; height: number };
const stages = ['Photos', 'Landmarks', 'Measurements', 'Review'];
export default function App() {
  const [step, setStep] = useState(0);
  const [photos, setPhotos] = useState<(Photo | null)[]>([null, null]);
  const photoRef = useRef(photos); photoRef.current = photos;
  const [start, setStart] = useState<Point>({ x: .5, y: .75 });
  const [end, setEnd] = useState<Point>({ x: .5, y: .25 });
  const [placed, setPlaced] = useState([false, false]);
  const [selected, setSelected] = useState<'start' | 'end'>('start');
  const [length, setLength] = useState('');
  const [values, setValues] = useState(['', '', '']);
  const [unreliable, setUnreliable] = useState([false, false, false]);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState('');
  const [exported, setExported] = useState(false);
  const svg = useRef<SVGSVGElement>(null);
  const dragging = useRef<'start' | 'end' | null>(null);
  useEffect(() => () => photoRef.current.forEach(p => p && URL.revokeObjectURL(p.url)), []);
  async function upload(file: File | undefined, index: number) {
    if (!file) return;
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 15 * 1024 * 1024) { setError('Choose a JPG, PNG, or WebP photo smaller than 15 MB.'); return; }
    const url = URL.createObjectURL(file);
    const img = new Image(); img.src = url;
    try { await img.decode(); } catch { URL.revokeObjectURL(url); setError('This photo could not be opened. Please choose another image.'); return; }
    setPhotos(prev => { if (prev[index]) URL.revokeObjectURL(prev[index]!.url); const next = [...prev]; next[index] = { file, url, width: img.naturalWidth, height: img.naturalHeight }; return next; });
    if (index === 0) { setPlaced([false, false]); setStart({ x: .5, y: .75 }); setEnd({ x: .5, y: .25 }); }
    setConfirmed(false); setExported(false); setError('');
  }
  const ready = step === 0 ? photos.every(Boolean) : step === 1 ? placed.every(Boolean) && landmarkDistance(start, end) > .03 : validMm(length) && values.every((v, i) => unreliable[i] || validMm(v));
  function position(event: React.PointerEvent<SVGSVGElement>) {
    const matrix = svg.current?.getScreenCTM(); if (!matrix) return;
    const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    const point = { x: Math.max(0, Math.min(1, p.x / 1000)), y: Math.max(0, Math.min(1, p.y / (1000 * photos[0]!.height / photos[0]!.width))) };
    const target = dragging.current || selected;
    if (target === 'start') setStart(point); else setEnd(point);
    setPlaced(prev => prev.map((v, i) => i === (target === 'start' ? 0 : 1) ? true : v));
    setConfirmed(false); setExported(false);
  }
  function download() {
    const record = { schema_version: 'capture.v1', units: 'mm', created_at: new Date().toISOString(), photos: photos.map((p, i) => ({ role: i === 0 ? 'generation' : 'review', filename: p!.file.name, width_px: p!.width, height_px: p!.height })), landmarks: { coordinate_system: 'normalized_image_xy_top_left', model_start: start, stub_endpoint: end }, length: { name: 'length', value: Number(length), units: 'mm' }, sections: fractions.map((f, i) => ({ name: `circumference_${f * 100}`, fraction: f, point: station(start, end, f), distance_from_start_mm: Number(length) * f, value: unreliable[i] ? null : Number(values[i]), units: 'mm', reliability: unreliable[i] ? 'unable_to_measure' : 'reported' })), flags: unreliable.some(Boolean) ? ['incomplete_measurements'] : [], reviewer_confirmed: true, scaler_compatibility: { ready: false, reason: 'Current scaler additionally requires maximum widths at all three stations. Capture record requires an adapter.' } };
    const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = 'epics-capture.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); setExported(true);
  }
  function overlay(editable: boolean) {
    const photo = photos[0]!; const height = 1000 * photo.height / photo.width;
    const a = { x: start.x * 1000, y: start.y * height }, b = { x: end.x * 1000, y: end.y * height };
    const d = Math.hypot(b.x - a.x, b.y - a.y) || 1; const normal = { x: -(b.y - a.y) / d * 90, y: (b.x - a.x) / d * 90 };
    return <svg ref={svg} viewBox={`0 0 1000 ${height}`} className={cn('photo-overlay', editable && 'editable')} role="img" aria-label="Main photo with model start, endpoint, and three circumference stations" onPointerDown={editable ? e => { e.currentTarget.setPointerCapture(e.pointerId); dragging.current = selected; position(e); } : undefined} onPointerMove={editable ? e => { if (dragging.current) position(e); } : undefined} onPointerUp={() => { dragging.current = null; }} onPointerCancel={() => { dragging.current = null; }}>
      <image href={photo.url} width="1000" height={height}/>
      <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="white" strokeWidth="4" strokeDasharray="10 8"/>
      {fractions.map((f, i) => { const p = { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f }; return <g key={f}><line x1={p.x - normal.x} y1={p.y - normal.y} x2={p.x + normal.x} y2={p.y + normal.y} stroke="#b6f176" strokeWidth="7"/><text x={p.x + normal.x + 12} y={p.y + normal.y} fill="#fff" fontSize="25" stroke="#183729" strokeWidth="4" paintOrder="stroke">{i + 1} · {f * 100}%</text></g>; })}
      {[a, b].map((p, i) => <g key={i}><circle cx={p.x} cy={p.y} r="19" fill={i === 0 ? '#b6f176' : '#fff'} stroke="#183729" strokeWidth="5" onPointerDown={editable ? e => { dragging.current = i === 0 ? 'start' : 'end'; setSelected(dragging.current); e.stopPropagation(); svg.current?.setPointerCapture(e.pointerId); } : undefined}/><text x={p.x + 28} y={p.y + 9} fontSize="27" fill="white" stroke="#183729" strokeWidth="4" paintOrder="stroke">{i === 0 ? 'Start' : 'Tip'}</text></g>)}
    </svg>;
  }
  return <div className="app-shell"><header className="topbar"><a href="/" className="brand"><span className="brand-mark"><Box size={22}/></span>EPICS<span className="brand-sub">REFERENCE LAB</span></a><Badge>Capture workspace</Badge></header>
    <main><div className="intro"><div><p className="eyebrow">PHOTO TO REFERENCE GEOMETRY</p><h1>A clear starting point.<br/><span>Measured by you.</span></h1><p className="intro-copy">Guide your capture, mark the limb, and record physical measurements for a 3D reference mesh.</p></div><div className="scope"><ShieldCheck size={22}/><p>For CAD review<br/><span>Reference geometry. Clinical fit is not verified.</span></p></div></div>
    <nav className="steps" aria-label="Capture progress">{stages.map((name, i) => <div key={name} className={cn('step', step === i && 'active', step > i && 'done')} aria-current={step === i ? 'step' : undefined}><span>{step > i ? <Check size={16}/> : `0${i + 1}`}</span>{name}</div>)}</nav>
    {error && <p role="alert" className="error">{error}</p>}
    <div className="workspace"><Card className="primary-card"><CardHeader><p className="eyebrow">STEP 0{step + 1} / 04</p><CardTitle>{['Two views. One reference.', 'Choose where the model begins.', 'Measure with a tape. Enter in mm.', 'Review your capture record.'][step]}</CardTitle><CardDescription>{['Use a clear main view of the fingerless residual limb and a side view for later comparison.', 'Select a landmark, then click the photo to place it. Drag a marker to refine its position.', 'The lines show where to wrap the tape. Circumference comes from your physical measurement.', 'Check the landmarks and measurements before exporting the handoff record.'][step]}</CardDescription></CardHeader>
    <CardContent>{step === 0 ? <div className="uploads">{['Main photo', 'Side photo'].map((name, i) => <div className="upload-slot" key={name}>{photos[i] ? <img src={photos[i]!.url} alt={name}/> : <div className="upload-placeholder"><Upload size={28}/><strong>{name}</strong><span>{i === 0 ? 'Used for TRELLIS.2 generation' : 'Used to review the result'}</span></div>}<div className="upload-footer"><div><strong>{name}</strong><small>{photos[i] ? photos[i]!.file.name : 'JPG, PNG, WebP · up to 15 MB'}</small></div><label className={cn(buttonVariants({ variant: 'outline' }), 'upload-label')}><input type="file" accept="image/jpeg,image/png,image/webp" aria-label={`Upload ${name.toLowerCase()}`} onChange={e => { void upload(e.target.files?.[0], i); e.target.value = ''; }}/>{photos[i] ? 'Replace' : 'Choose photo'}</label></div></div>)}</div> : <div className="capture-grid"><div className="photo-panel">{overlay(step === 1)}<div className="photo-caption"><Move size={14}/>{step === 1 ? 'Click to place · drag to adjust' : 'Main photo · recorded measurement stations'}</div></div><div className="controls">
    {step === 1 && <><div className="landmark-select">{(['start', 'end'] as const).map((key, i) => <Button key={key} variant={selected === key ? 'default' : 'outline'} onClick={() => setSelected(key)}>{placed[i] && <Check size={16}/>} {i === 0 ? '1. Model start' : '2. Furthest tip'}</Button>)}</div><p className="muted">Place the start at the proximal point where the model should begin. Place the tip at the furthest point of the stub.</p><div className="note"><Ruler size={18}/><p>The three guide lines follow your landmarks at 25%, 50%, and 75%. Confirm their physical distances after entering length.</p></div><fieldset className="coordinate-fields"><legend>Fine adjustment (% of photo)</legend>{(['start', 'end'] as const).map(key => <div key={key}><strong>{key === 'start' ? 'Start' : 'Tip'}</strong>{(['x', 'y'] as const).map(axis => <label key={axis}>{axis.toUpperCase()}<input type="number" min="0" max="100" step="0.1" value={Number(((key === 'start' ? start : end)[axis] * 100).toFixed(1))} onChange={e => { const v = Number(e.target.value); if (e.target.value !== '' && v >= 0 && v <= 100) { (key === 'start' ? setStart : setEnd)(p => ({ ...p, [axis]: v / 100 })); setPlaced(p => p.map((old, i) => i === (key === 'start' ? 0 : 1) ? true : old)); } }}/></label>)}</div>)}</fieldset>{placed.every(Boolean) && landmarkDistance(start, end) <= .03 && <p className="error">Start and tip need to be distinct points.</p>}</>}
    {step === 2 && <><label className="measurement-label">Overall length <span>mm</span><input type="number" min="0" max="2000" step="any" placeholder="e.g. 120" value={length} onChange={e => { setLength(e.target.value); setConfirmed(false); }}/></label><p className="field-help">Straight distance from start to tip along the limb. Do not follow the skin’s curve.</p>{fractions.map((f, i) => <div className="measurement-row" key={f}><label className="measurement-label"><span className="station-number">{i + 1}</span> Circumference · {f * 100}%<span>mm</span><input type="number" min="0" max="2000" step="any" disabled={unreliable[i]} placeholder="Tape measurement" value={values[i]} onChange={e => { setValues(v => v.map((old, j) => j === i ? e.target.value : old)); setConfirmed(false); }}/></label><small>{validMm(length) ? `${(Number(length) * f).toFixed(1)} mm from the start` : 'Enter length to see the physical station distance'}</small><label className="checkbox-label"><input type="checkbox" checked={unreliable[i]} onChange={e => { setUnreliable(v => v.map((old, j) => j === i ? e.target.checked : old)); setConfirmed(false); }}/>Unable to measure reliably</label></div>)}<p className="field-help">Use positive values up to 2,000 mm. Missing stations will be flagged for review.</p></>}
    {step === 3 && <><Badge>{unreliable.some(Boolean) ? 'Review needed · incomplete measurements' : 'Measurements recorded'}</Badge><dl className="summary"><div><dt>Overall length</dt><dd>{length} mm</dd></div>{fractions.map((f, i) => <div key={f}><dt>Circumference · {f * 100}%</dt><dd>{unreliable[i] ? 'Unable to measure' : `${values[i]} mm`}</dd></div>)}</dl><img className="side-preview" src={photos[1]!.url} alt="Side photo for future independent comparison"/><p className="field-help">Side photo · retained in this session for review</p><label className="checkbox-label confirm"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/>I have checked the positions and the physical measurements.</label><Button disabled={!confirmed} onClick={download}><Download size={17}/>Download capture JSON</Button>{exported && <p role="status" className="success">Capture record downloaded. Keep both original photos with this record.</p>}</>}
    </div></div>}</CardContent>
    <footer className="card-footer"><Button variant="ghost" disabled={step === 0} onClick={() => { setStep(s => s - 1); setError(''); }}><ArrowLeft size={16}/>Back</Button><span>{step === 0 ? 'Photos stay in your browser for this session.' : 'Physical measurements · millimetres'}</span>{step < 3 && <Button disabled={!ready} onClick={() => { setStep(s => s + 1); setError(''); }}>{step === 2 ? 'Review capture' : 'Continue'}<ArrowRight size={16}/></Button>}</footer></Card>
    <aside><Card><CardHeader><Badge>CAPTURE GUIDE</Badge><CardTitle>{step === 0 ? 'A good capture starts here.' : 'Make each measurement count.'}</CardTitle></CardHeader><CardContent><ol className="guide"><li><strong>Choose a clear view</strong><p>Use even light and a simple background. Include the full intended stub with no remaining fingers.</p></li><li><strong>Use the same start point</strong><p>The photo landmark, physical measurement, and generated mesh cutoff must correspond.</p></li><li><strong>Wrap perpendicular to length</strong><p>Use a tape at each station. The image lines are location guides; pixels do not measure circumference.</p></li></ol></CardContent></Card><div className="integration-note"><Box size={20}/><strong>Generation comes next</strong><p>This flow prepares a capture record. TRELLIS.2 generation, scaling, 3D preview, and Samantha’s comparison report are awaiting integration.</p><p>Aarsh’s current scaler also requires three maximum widths. This four-measurement record is not yet scaler-ready.</p></div></aside></div><footer className="page-footer">EPICS / Reference geometry workflow<span>Uploaded photos and entries clear when you reload.</span></footer></main></div>;
}
