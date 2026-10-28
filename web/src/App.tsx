import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, Download, Move, Ruler, Upload, Box, ShieldCheck, Loader2, Lock, RotateCcw } from 'lucide-react';
import { Badge, Button, buttonVariants, Card, CardContent, CardDescription, CardHeader, CardTitle } from './components/ui';
import { cn } from './lib/utils';
import { ModelPreview } from './components/ModelPreview';
import { fractions, landmarkDistance, sectionProblem, station, stubMeasurements, validMm, type Point } from './capture';

type Photo = { file: File; url: string; width: number; height: number };
type Job = { id: string; status: 'queued' | 'running' | 'done' | 'interrupted'; created: string; started: string | null; finished: string | null; position: number | null; error: string | null; files: string[]; options: { outer_shell: boolean; remesh: boolean }; result?: { status: string; failed_stage: string | null; error: string | null; hint: string | null; review_status: string; elapsed_s: number | null; scale: { status: string; message: string | null } | null; cleanup: Cleanup | null; validation: string | null; limitations: string[] } };
type Prep = { id: string; key: string; cap: 'oval' | 'straight'; warnings: string[] };
type Cleanup = { status: string; reason: string | null; pieces: { piece: number; faces: number; decision: string | null; review_flag: string | null }[]; holes_filled: number; faces_removed: number; welded: number[]; fused: number[]; texture: { new_vertices?: number; mean_colour_difference?: number } | null };
// Previews in review order; a scaled mesh is Z-up, the others keep TRELLIS.2's frame.
const previews: [string, string][] = [['scaled.glb', 'Scaled mesh'], ['outer_shell.glb', 'Cleaned outer wall · unscaled'], ['outer_shell_review.glb', 'Cleanup review · removed pieces red, repaired patches yellow · unscaled'], ['generated.glb', 'Original TRELLIS.2 output · unscaled, not matched to your measurements']];
// Server API (src/epics_geometry/server.py); throws with the server's message on failure.
async function api<T>(path: string, init?: RequestInit): Promise<T> { const r = await fetch(path, { credentials: 'same-origin', ...init }); const body = await r.json().catch(() => ({})); if (!r.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${r.status})`); return body as T; }
const resultLabels: Record<string, string> = { complete: 'Scaled mesh ready · pending human review', cleanup_refused: 'Cleanup stopped · original mesh kept for inspection', validation_failed: 'Scaled, but independent validation failed', error: 'Run stopped with an error' };
const stages = ['Photos', 'Landmarks', 'Measurements', 'Review'];
function CleanupReport({ cleanup }: { cleanup: Cleanup }) {
  const repairs = [cleanup.faces_removed && `${cleanup.faces_removed} malformed faces removed`, cleanup.holes_filled && `${cleanup.holes_filled} small holes filled`, cleanup.welded.length && `welded piece ${cleanup.welded.join(', ')}`, cleanup.fused.length && `fused piece ${cleanup.fused.join(', ')}`].filter(Boolean);
  return <div className="cleanup"><p className="eyebrow">OUTER-WALL CLEANUP · {cleanup.status.toUpperCase()}</p>
    {cleanup.reason && <p className="error">{cleanup.reason}</p>}
    {repairs.length > 0 && <p className="field-help">{repairs.join(' · ')}{cleanup.texture?.new_vertices ? ` · ${cleanup.texture.new_vertices} new vertices textured` : ''}</p>}
    <table><thead><tr><th>Piece</th><th>Faces</th><th>Decision</th></tr></thead><tbody>{cleanup.pieces.map(p => <tr key={p.piece} className={p.review_flag ? 'flag' : undefined}><td>{p.piece}</td><td>{p.faces.toLocaleString()}</td><td>{p.decision ?? 'not reached'}{p.review_flag && <strong> · {p.review_flag}</strong>}</td></tr>)}</tbody></table></div>;
}

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
  const [widths, setWidths] = useState(['', '', '']);
  const [unreliable, setUnreliable] = useState([false, false, false]);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState('');
  const [exported, setExported] = useState(false);
  const [auth, setAuth] = useState<boolean | null>(null);
  const [passcode, setPasscode] = useState('');
  const [outerShell, setOuterShell] = useState(false);
  const [remesh, setRemesh] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  // Photo preparation: the approved preview must match the current photo, markers and end style.
  const [usePrep, setUsePrep] = useState(true);
  const [cap, setCap] = useState<'oval' | 'straight'>('straight');
  const [prep, setPrep] = useState<Prep | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [approved, setApproved] = useState(false);
  const [showFace, setShowFace] = useState(true);
  const prepKey = photos[0] ? JSON.stringify([photos[0].file.name, photos[0].file.size, photos[0].file.lastModified, start, end, cap]) : '';
  const prepCurrent = !!prep && prep.key === prepKey;
  const prepRef = useRef<Prep | null>(null); prepRef.current = prep;
  const svg = useRef<SVGSVGElement>(null);
  const dragging = useRef<'start' | 'end' | null>(null);
  const dragOffset = useRef<Point>({ x: 0, y: 0 });
  useEffect(() => () => photoRef.current.forEach(p => p && URL.revokeObjectURL(p.url)), []);
  useEffect(() => { api<{ authenticated: boolean }>('/api/session').then(r => setAuth(r.authenticated), () => setAuth(false)); }, []);
  useEffect(() => {
    if (!jobId || !auth) return;
    let timer = 0, live = true;
    const poll = () => api<Job>(`/api/jobs/${jobId}`).then(j => { if (!live) return; setJob(j); if (j.status === 'queued' || j.status === 'running') timer = window.setTimeout(poll, 3000); }, e => { if (live) setError(e.message); });
    poll(); return () => { live = false; clearTimeout(timer); };
  }, [jobId, auth]);
  // Jobs are not kept: leaving the page (refresh, close, navigate) deletes the photos and meshes on the server.
  useEffect(() => {
    if (!jobId) return;
    const warn = (e: BeforeUnloadEvent) => { if (!downloaded) e.preventDefault(); };
    const discard = () => navigator.sendBeacon(`/api/jobs/${jobId}/discard`);
    addEventListener('beforeunload', warn); addEventListener('pagehide', discard);
    return () => { removeEventListener('beforeunload', warn); removeEventListener('pagehide', discard); };
  }, [jobId, downloaded]);
  // A preview for an older photo, marker position or end style is discarded; it must be prepared and approved again.
  useEffect(() => { if (prep && prep.key !== prepKey) { navigator.sendBeacon(`/api/preparations/${prep.id}/discard`); setPrep(null); setApproved(false); } }, [prepKey, prep]);
  useEffect(() => { const drop = () => { if (prepRef.current) navigator.sendBeacon(`/api/preparations/${prepRef.current.id}/discard`); }; addEventListener('pagehide', drop); return () => removeEventListener('pagehide', drop); }, []);
  async function preparePhoto() {
    const form = new FormData(); form.append('photo', photos[0]!.file, photos[0]!.file.name); form.append('landmarks', JSON.stringify({ start, tip: end })); form.append('cap', cap);
    const key = prepKey; setPreparing(true); setError(''); setApproved(false);
    try { const r = await api<Omit<Prep, 'key'>>('/api/preparations', { method: 'POST', body: form }); if (prep) navigator.sendBeacon(`/api/preparations/${prep.id}/discard`); setPrep({ ...r, key }); } catch (e) { setError((e as Error).message); } finally { setPreparing(false); }
  }
  async function login() {
    try { await api('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ passcode }) }); setAuth(true); setPasscode(''); setError(''); } catch (e) { setError((e as Error).message); }
  }
  async function submit() {
    const form = new FormData(); form.append('capture', JSON.stringify(record())); photos.forEach(p => form.append('photos', p!.file, p!.file.name)); form.append('outer_shell', String(outerShell)); form.append('remesh', String(remesh));
    if (usePrep && prep) form.append('preparation', prep.id);
    setSubmitting(true); setError('');
    try { const j = await api<Job>('/api/jobs', { method: 'POST', body: form }); setJob(j); setJobId(j.id); setDownloaded(false); if (usePrep) setPrep(null); } catch (e) { setError((e as Error).message); } finally { setSubmitting(false); }
  }
  function newCapture() { if (jobId) navigator.sendBeacon(`/api/jobs/${jobId}/discard`); setJob(null); setJobId(null); setStep(0); setConfirmed(false); setExported(false); setError(''); }
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
  const ready = step === 0 ? photos.every(Boolean) : step === 1 ? placed.every(Boolean) && landmarkDistance(start, end) > .03 : validMm(length) && values.every((v, i) => unreliable[i] || (validMm(v) && validMm(widths[i]) && !sectionProblem(v, widths[i])));
  function photoPoint(event: React.PointerEvent<SVGElement>): Point | null {
    const matrix = svg.current?.getScreenCTM(); if (!matrix) return null;
    const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return { x: p.x / 1000, y: p.y / (1000 * photos[0]!.height / photos[0]!.width) };
  }
  function position(event: React.PointerEvent<SVGSVGElement>) {
    const p = photoPoint(event); if (!p) return;
    const point = { x: Math.max(0, Math.min(1, p.x + dragOffset.current.x)), y: Math.max(0, Math.min(1, p.y + dragOffset.current.y)) };
    const target = dragging.current || selected;
    if (target === 'start') setStart(point); else setEnd(point);
    setPlaced(prev => prev.map((v, i) => i === (target === 'start' ? 0 : 1) ? true : v));
    setConfirmed(false); setExported(false);
  }
  function record() {
    return { schema_version: 'capture.v1', units: 'mm', created_at: new Date().toISOString(), photos: photos.map((p, i) => ({ role: i === 0 ? 'generation' : 'review', filename: p!.file.name, width_px: p!.width, height_px: p!.height })), landmarks: { coordinate_system: 'normalized_image_xy_top_left', model_start: start, stub_endpoint: end }, length: { name: 'length', value: Number(length), units: 'mm' }, sections: fractions.map((f, i) => ({ name: `circumference_${f * 100}`, fraction: f, point: station(start, end, f), distance_from_start_mm: Number(length) * f, value: unreliable[i] ? null : Number(values[i]), max_width_mm: unreliable[i] ? null : Number(widths[i]), units: 'mm', reliability: unreliable[i] ? 'unable_to_measure' : 'reported' })), flags: unreliable.some(Boolean) ? ['incomplete_measurements'] : [], reviewer_confirmed: true, stub_measurements: stubMeasurements(length, values, widths, unreliable) };
  }
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(record(), null, 2)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = 'capture.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); setExported(true);
  }
  function prepPanel() {
    return <div className="prep"><p className="eyebrow">PHOTO PREPARATION</p>
      <label className="checkbox-label"><input type="checkbox" checked={usePrep} onChange={e => setUsePrep(e.target.checked)}/>Prepare the photo before generation: remove the background, cut across Start, and finish the cut end</label>
      {usePrep && <>
        <div className="prep-options" role="radiogroup" aria-label="End style">{(['straight', 'oval'] as const).map(c => <label key={c} className="checkbox-label"><input type="radio" name="cap" checked={cap === c} onChange={() => setCap(c)}/>{c === 'oval' ? 'Oval end face (experimental: in testing it added a lump at Start)' : 'Straight cut (recommended)'}</label>)}</div>
        <Button variant="outline" disabled={preparing} onClick={() => void preparePhoto()}>{preparing ? <Loader2 size={17} className="spin"/> : <Move size={17}/>}{preparing ? 'Preparing…' : prepCurrent ? 'Prepare again' : 'Prepare photo'}</Button>
        {prepCurrent && prep && <>
          <div className="prep-compare"><figure><img src={photos[0]!.url} alt="Original main photo"/><figcaption>Original</figcaption></figure><figure><img src={`/api/preparations/${prep.id}/files/${showFace && prep.cap === 'oval' ? 'preview_face.png' : 'preview.png'}`} alt="Prepared photo on grey"/><figcaption>Prepared{prep.cap === 'oval' && showFace ? ' · synthetic end face outlined in magenta' : ''}</figcaption></figure></div>
          {prep.cap === 'oval' && <label className="checkbox-label"><input type="checkbox" checked={showFace} onChange={e => setShowFace(e.target.checked)}/>Outline the synthetic end face</label>}
          {prep.warnings.map(w => <p key={w} className="error">{w}</p>)}
          <p className="field-help">The end face is a visual cue that the limb ends at Start, not the real cross-section. If part of the limb is missing or background remains, go back to adjust Start/Tip or replace the photo.</p>
          <label className="checkbox-label confirm"><input type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)}/>The prepared photo shows the whole limb from Start to Tip, with nothing missing or added beyond the end face.</label>
        </>}
        {!prepCurrent && <p className="field-help">Generation uses exactly the preview you approve. Changing the photo, either marker, or the end style needs a new preview.</p>}
      </>}
    </div>;
  }
  function overlay(editable: boolean) {
    const photo = photos[0]!; const height = 1000 * photo.height / photo.width;
    const a = { x: start.x * 1000, y: start.y * height }, b = { x: end.x * 1000, y: end.y * height };
    const d = Math.hypot(b.x - a.x, b.y - a.y) || 1; const normal = { x: -(b.y - a.y) / d * 90, y: (b.x - a.x) / d * 90 };
    const lineNormal = { x: normal.x / 90 * Math.hypot(1000, height), y: normal.y / 90 * Math.hypot(1000, height) };
    return <svg ref={svg} viewBox={`0 0 1000 ${height}`} className={cn('photo-overlay', editable && 'editable')} role="img" aria-label="Main photo with start and tip guide lines and three circumference stations" onPointerDown={editable ? e => { e.currentTarget.setPointerCapture(e.pointerId); dragging.current = selected; dragOffset.current = { x: 0, y: 0 }; position(e); } : undefined} onPointerMove={editable ? e => { if (dragging.current) position(e); } : undefined} onPointerUp={() => { dragging.current = null; }} onPointerCancel={() => { dragging.current = null; }}>
      <image href={photo.url} width="1000" height={height}/>
      {placed.every(Boolean) && <><line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="white" strokeWidth="4" strokeDasharray="10 8" pointerEvents="none"/>
      {fractions.map((f, i) => { const p = { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f }; return <g key={f} pointerEvents="none"><line x1={p.x - normal.x} y1={p.y - normal.y} x2={p.x + normal.x} y2={p.y + normal.y} stroke="#b6f176" strokeWidth="7"/><text x={p.x + normal.x + 12} y={p.y + normal.y} fill="#fff" fontSize="25" stroke="#183729" strokeWidth="4" paintOrder="stroke">{i + 1} · {f * 100}%</text></g>; })}</>}
      {[a, b].map((p, i) => placed[i] && <g key={i}>
        <line x1={p.x - lineNormal.x} y1={p.y - lineNormal.y} x2={p.x + lineNormal.x} y2={p.y + lineNormal.y} stroke="#183729" strokeWidth="10" pointerEvents="none"/>
        <line x1={p.x - lineNormal.x} y1={p.y - lineNormal.y} x2={p.x + lineNormal.x} y2={p.y + lineNormal.y} stroke={i === 0 ? '#b6f176' : '#fff'} strokeWidth="5" pointerEvents="none"/>
        {editable && <line x1={p.x - lineNormal.x} y1={p.y - lineNormal.y} x2={p.x + lineNormal.x} y2={p.y + lineNormal.y} stroke="transparent" strokeWidth="36" onPointerDown={e => { const point = photoPoint(e); if (!point) return; dragging.current = i === 0 ? 'start' : 'end'; const marker = i === 0 ? start : end; dragOffset.current = { x: marker.x - point.x, y: marker.y - point.y }; setSelected(dragging.current); e.stopPropagation(); svg.current?.setPointerCapture(e.pointerId); }}/>}
        <text x={Math.max(90, Math.min(910, p.x))} y={Math.max(35, Math.min(height - 15, p.y - 20))} textAnchor="middle" fontSize="27" fill="white" stroke="#183729" strokeWidth="5" paintOrder="stroke" pointerEvents="none">{i === 0 ? 'Start' : 'Tip'}</text>
      </g>)}
    </svg>;
  }
  return <div className="app-shell"><header className="topbar"><a href="/" className="brand"><span className="brand-mark"><Box size={22}/></span>EPICS<span className="brand-sub">REFERENCE LAB</span></a><Badge>Capture workspace</Badge></header>
    <main><div className="intro"><div><p className="eyebrow">PHOTO TO REFERENCE GEOMETRY</p><h1>A clear starting point.<br/><span>Measured by you.</span></h1><p className="intro-copy">Guide your capture, mark the limb, and record physical measurements for a 3D reference mesh.</p></div><div className="scope"><ShieldCheck size={22}/><p>For CAD review<br/><span>Reference geometry. Clinical fit is not verified.</span></p></div></div>
    <nav className="steps" aria-label="Capture progress">{stages.map((name, i) => <div key={name} className={cn('step', step === i && 'active', step > i && 'done')} aria-current={step === i ? 'step' : undefined}><span>{step > i ? <Check size={16}/> : `0${i + 1}`}</span>{name}</div>)}</nav>
    {error && <p role="alert" className="error">{error}</p>}
    {auth === null ? null : !auth ? <Card className="primary-card gate"><CardHeader><p className="eyebrow">TEAM ACCESS</p><CardTitle>Enter the team passcode.</CardTitle><CardDescription>This workspace sends photos to the EPICS lab server for generation, so it is limited to the project team.</CardDescription></CardHeader><CardContent><form className="gate-form" onSubmit={e => { e.preventDefault(); void login(); }}><label className="measurement-label"><Lock size={15}/> Passcode<input type="password" autoComplete="current-password" autoFocus value={passcode} onChange={e => setPasscode(e.target.value)}/></label><Button type="submit" disabled={!passcode}>Unlock</Button></form></CardContent></Card>
    : jobId ? <Card className="primary-card"><CardHeader><p className="eyebrow">GENERATION JOB · {jobId.slice(0, 8)}</p><CardTitle>{!job ? 'Loading job…' : job.status === 'queued' ? 'Waiting for the GPU.' : job.status === 'running' ? 'Generating on the DGX Spark.' : job.status === 'interrupted' ? 'This run was interrupted.' : job.result?.status === 'complete' ? 'Your reference mesh is ready.' : 'No scaled mesh this time.'}</CardTitle><CardDescription>{job?.status === 'done' ? 'Reference geometry for device design only. A designer must review it before any use; it is not medically or metrically verified.' : 'TRELLIS.2 generation, stub scaling and independent validation run in order. This page updates on its own.'}</CardDescription></CardHeader>
    <CardContent>{job && (job.status === 'queued' || job.status === 'running') && <div className="note"><Loader2 size={18} className="spin"/><p>{job.status === 'queued' ? `Position ${job.position ?? '…'} in the queue. One job runs at a time.` : `Started ${new Date(job.started!).toLocaleTimeString()}. Generation usually takes a few minutes; the first run after a restart also loads the model.`}</p></div>}
    {job?.status === 'interrupted' && <p className="error">The server restarted while this job was waiting or running. Start a new capture and submit it again.</p>}
    {job?.status === 'done' && <>{job.result ? <><Badge>{resultLabels[job.result.status] ?? `Stopped at ${job.result.failed_stage ?? 'unknown'} · ${job.result.status.replaceAll('_', ' ')}`}</Badge><dl className="summary"><div><dt>Review status</dt><dd>{job.result.review_status.replaceAll('_', ' ')}</dd></div>{job.result.scale && <div><dt>Scaling</dt><dd>{job.result.scale.status.replaceAll('_', ' ')}{job.result.scale.message ? ` · ${job.result.scale.message}` : ''}</dd></div>}{job.result.validation && <div><dt>Validation</dt><dd>{job.result.validation}</dd></div>}{job.result.elapsed_s != null && <div><dt>Run time</dt><dd>{(job.result.elapsed_s / 60).toFixed(1)} min</dd></div>}</dl>{job.result.error && <p className="error">{job.result.error}</p>}{job.result.hint && <div className="note"><Ruler size={18}/><p>{job.result.hint}</p></div>}{job.result.cleanup && <CleanupReport cleanup={job.result.cleanup}/>}</> : <p className="error">{job.error ?? 'The run finished without a manifest.'}</p>}
    <div className="previews">{previews.filter(([f]) => job.files.includes(f)).map(([f, label]) => <ModelPreview key={f} src={`/api/jobs/${job.id}/files/${f}`} zUp={f === 'scaled.glb'} label={label}/>)}</div>
    {job.files.length > 0 && <div className="downloads">{job.files.map(f => <a key={f} className={buttonVariants({ variant: f === 'scaled.glb' ? 'default' : 'outline' })} href={`/api/jobs/${job.id}/files/${f}`} download={f} onClick={() => setDownloaded(true)}><Download size={16}/>{f}</a>)}</div>}
    {job.result && <ul className="field-help">{job.result.limitations.map(l => <li key={l}>{l}</li>)}</ul>}</>}</CardContent>
    <footer className="card-footer"><Button variant="ghost" onClick={newCapture}><RotateCcw size={16}/>New capture</Button><span>Leaving or refreshing this page deletes the job. Download what you need first.</span></footer></Card>
    : <div className="workspace"><Card className="primary-card"><CardHeader><p className="eyebrow">STEP 0{step + 1} / 04</p><CardTitle>{['Two views. One reference.', 'Choose where the model begins.', 'Measure with a tape. Enter in mm.', 'Review your capture record.'][step]}</CardTitle><CardDescription>{['Use a clear main view of the fingerless residual limb and a side view for later comparison.', 'Select Start or Tip, then click the photo. Drag the line to adjust its position.', 'The lines show where to wrap the tape. Circumference comes from your physical measurement.', 'Check the landmarks and measurements before exporting the handoff record.'][step]}</CardDescription></CardHeader>
    <CardContent>{step === 0 ? <div className="uploads">{['Main photo', 'Side photo'].map((name, i) => <div className="upload-slot" key={name}>{photos[i] ? <img src={photos[i]!.url} alt={name}/> : <div className="upload-placeholder"><Upload size={28}/><strong>{name}</strong><span>{i === 0 ? 'Used for TRELLIS.2 generation' : 'Used to review the result'}</span></div>}<div className="upload-footer"><div><strong>{name}</strong><small>{photos[i] ? photos[i]!.file.name : 'JPG, PNG, WebP · up to 15 MB'}</small></div><label className={cn(buttonVariants({ variant: 'outline' }), 'upload-label')}><input type="file" accept="image/jpeg,image/png,image/webp" aria-label={`Upload ${name.toLowerCase()}`} onChange={e => { void upload(e.target.files?.[0], i); e.target.value = ''; }}/>{photos[i] ? 'Replace' : 'Choose photo'}</label></div></div>)}</div> : <div className="capture-grid"><div className="photo-panel">{overlay(step === 1)}<div className="photo-caption"><Move size={14}/>{step === 1 ? 'Click to place · drag to adjust' : 'Main photo · recorded measurement stations'}</div></div><div className="controls">
    {step === 1 && <><div className="landmark-select">{(['start', 'end'] as const).map((key, i) => <Button key={key} variant={selected === key ? 'default' : 'outline'} onClick={() => setSelected(key)}>{placed[i] && <Check size={16}/>} {i === 0 ? '1. Model start' : '2. Furthest tip'}</Button>)}</div><p className="muted">Place Start where the model should begin. Place Tip at the furthest visible edge of the limb, even when it is in the middle of the photo. Both lines cross the limb at those locations.</p><div className="note"><Ruler size={18}/><p>{usePrep ? 'Start and Tip guide your measurements, and before generation the photo is cut across Start (you preview it on the Review step). Tip is never cut.' : 'Start and Tip guide your measurements; they do not crop the photo or cut the generated mesh.'} The three shorter lines mark 25%, 50%, and 75% of the distance.</p></div><fieldset className="coordinate-fields"><legend>Fine adjustment (% of photo)</legend>{(['start', 'end'] as const).map(key => <div key={key}><strong>{key === 'start' ? 'Start' : 'Tip'}</strong>{(['x', 'y'] as const).map(axis => <label key={axis}>{axis.toUpperCase()}<input type="number" min="0" max="100" step="0.1" value={Number(((key === 'start' ? start : end)[axis] * 100).toFixed(1))} onChange={e => { const v = Number(e.target.value); if (e.target.value !== '' && v >= 0 && v <= 100) { (key === 'start' ? setStart : setEnd)(p => ({ ...p, [axis]: v / 100 })); setPlaced(p => p.map((old, i) => i === (key === 'start' ? 0 : 1) ? true : old)); } }}/></label>)}</div>)}</fieldset>{placed.every(Boolean) && landmarkDistance(start, end) <= .03 && <p className="error">Start and tip need to be distinct points.</p>}</>}
    {step === 2 && <><label className="measurement-label">Overall length <span>mm</span><input type="number" min="0" max="2000" step="any" placeholder="e.g. 120" value={length} onChange={e => { setLength(e.target.value); setConfirmed(false); }}/></label><p className="field-help">Straight distance from start to tip along the limb. Do not follow the skin’s curve.</p>{fractions.map((f, i) => <div className="measurement-row" key={f}><label className="measurement-label"><span className="station-number">{i + 1}</span> Circumference · {f * 100}%<span>mm</span><input type="number" min="0" max="2000" step="any" disabled={unreliable[i]} placeholder="Tape measurement" value={values[i]} onChange={e => { setValues(v => v.map((old, j) => j === i ? e.target.value : old)); setConfirmed(false); }}/></label><label className="measurement-label">Max width · {f * 100}%<span>mm</span><input type="number" min="0" max="2000" step="any" disabled={unreliable[i]} placeholder="Caliper span" value={widths[i]} onChange={e => { setWidths(v => v.map((old, j) => j === i ? e.target.value : old)); setConfirmed(false); }}/></label>{!unreliable[i] && sectionProblem(values[i], widths[i]) && <p className="error">{sectionProblem(values[i], widths[i])}</p>}<small>{validMm(length) ? `${(Number(length) * f).toFixed(1)} mm from the start` : 'Enter length to see the physical station distance'}</small><label className="checkbox-label"><input type="checkbox" checked={unreliable[i]} onChange={e => { setUnreliable(v => v.map((old, j) => j === i ? e.target.checked : old)); setConfirmed(false); }}/>Unable to measure reliably</label></div>)}<p className="field-help">Max width is the largest straight-across span of the same cross-section (caliper or two parallel straightedges), not a curved measurement. Use positive values up to 2,000 mm. Missing stations will be flagged for review and cannot be scaled.</p></>}
    {step === 3 && <><Badge>{unreliable.some(Boolean) ? 'Review needed · incomplete measurements, cannot be scaled' : 'Measurements recorded · ready for generation'}</Badge><dl className="summary"><div><dt>Overall length</dt><dd>{length} mm</dd></div>{fractions.map((f, i) => <div key={f}><dt>Circumference · {f * 100}%</dt><dd>{unreliable[i] ? 'Unable to measure' : `${values[i]} mm · width ${widths[i]} mm`}</dd></div>)}</dl><img className="side-preview" src={photos[1]!.url} alt="Side photo for future independent comparison"/><p className="field-help">Side photo · retained in this session for review</p><label className="checkbox-label confirm"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/>I have checked the positions and the physical measurements.</label><label className="checkbox-label"><input type="checkbox" checked={outerShell} onChange={e => setOuterShell(e.target.checked)}/>Clean up the mesh: keep the outer wall, drop hidden or detached pieces, repair small defects (experimental)</label><label className="checkbox-label"><input type="checkbox" checked={!remesh} onChange={e => setRemesh(!e.target.checked)}/>Skip remeshing during GLB export</label>{prepPanel()}<Button disabled={!confirmed || !stubMeasurements(length, values, widths, unreliable) || submitting || (usePrep && !(prepCurrent && approved))} onClick={() => void submit()}>{submitting ? <Loader2 size={17} className="spin"/> : <Box size={17}/>}{submitting ? 'Uploading…' : 'Generate reference mesh'}</Button><p className="field-help">Uploads both photos and this record to the EPICS lab server (DGX Spark), which runs TRELLIS.2, scaling and validation. Photos and meshes are deleted from the server as soon as you leave the result page.</p><Button variant="outline" disabled={!confirmed} onClick={download}><Download size={17}/>Download capture.json only</Button>{exported && <p role="status" className="success">Capture record downloaded. Keep it with both original photos ({photos.map(p => p!.file.name).join(', ')}) to run the pipeline elsewhere.</p>}</>}
    </div></div>}</CardContent>
    <footer className="card-footer"><Button variant="ghost" disabled={step === 0} onClick={() => { setStep(s => s - 1); setError(''); }}><ArrowLeft size={16}/>Back</Button><span>{step === 0 ? 'Photos stay in your browser until you submit.' : 'Physical measurements · millimetres'}</span>{step < 3 && <Button disabled={!ready} onClick={() => { setStep(s => s + 1); setError(''); }}>{step === 2 ? 'Review capture' : 'Continue'}<ArrowRight size={16}/></Button>}</footer></Card>
    <aside><Card><CardHeader><Badge>CAPTURE GUIDE</Badge><CardTitle>{step === 0 ? 'A good capture starts here.' : 'Make each measurement count.'}</CardTitle></CardHeader><CardContent><ol className="guide"><li><strong>Choose a clear view</strong><p>Use even light and a simple background. Include the full intended stub with no remaining fingers.</p></li><li><strong>Use the same start point</strong><p>The photo landmark, physical measurement, and generated mesh cutoff must correspond.</p></li><li><strong>Wrap perpendicular to length</strong><p>Use a tape at each station. The image lines are location guides; pixels do not measure circumference.</p></li></ol></CardContent></Card><div className="integration-note"><Box size={20}/><strong>Generation runs on the lab server</strong><p>Submitting sends both photos and the capture record to the EPICS DGX Spark. TRELLIS.2 generates from the main photo, then the scaler matches all seven measurements and validation re-checks them.</p><p>3D preview and Samantha’s comparison report are not integrated yet; download the GLB to review it.</p></div></aside></div>}<footer className="page-footer">EPICS / Reference geometry workflow<span>Unsubmitted photos and entries clear when you reload.</span></footer></main></div>;
}
