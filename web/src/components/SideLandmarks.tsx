import { useState } from 'react';
import { Button } from './ui';
import type { CapturePhoto, Point } from '../capture';

export function SideLandmarks({ photo, start, tip, onChange }: {
  photo: CapturePhoto; start: Point | null; tip: Point | null;
  onChange: (start: Point | null, tip: Point | null) => void;
}) {
  const [selected, setSelected] = useState<'start' | 'tip'>('start');
  const [dragging, setDragging] = useState(false);
  const height = 1000 * photo.height / photo.width;
  function place(event: React.PointerEvent<SVGSVGElement>) {
    const matrix = event.currentTarget.getScreenCTM();
    if (!matrix) return;
    const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    const point = { x: Math.max(0, Math.min(1, p.x / 1000)), y: Math.max(0, Math.min(1, p.y / height)) };
    onChange(selected === 'start' ? point : start, selected === 'tip' ? point : tip);
  }
  const a = start && { x: start.x * 1000, y: start.y * height };
  const b = tip && { x: tip.x * 1000, y: tip.y * height };
  const dx = a && b ? b.x - a.x : 0, dy = a && b ? b.y - a.y : -height;
  const span = Math.hypot(dx, dy) || 1;
  const nx = -dy / span * Math.hypot(1000, height), ny = dx / span * Math.hypot(1000, height);
  return <section className="side-landmarks" aria-label="Side photo landmarks">
    <h3>Mark the same Start and Tip on the side photo</h3>
    <p className="field-help">Take this view about 90° around the limb from the main photo, with the limb in the same pose. Keep the full tip visible and the camera broadside, not looking along the limb. Use the same physical start location in both photos.</p>
    <div className="landmark-select">
      <Button variant={selected === 'start' ? 'default' : 'outline'} onClick={() => setSelected('start')}>Side Start{start ? ' · placed' : ''}</Button>
      <Button variant={selected === 'tip' ? 'default' : 'outline'} onClick={() => setSelected('tip')}>Side Tip{tip ? ' · placed' : ''}</Button>
    </div>
    <svg className="photo-overlay editable" viewBox={`0 0 1000 ${height}`} role="img" aria-label="Side photo: click or drag to place the selected landmark"
      onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); setDragging(true); place(e); }}
      onPointerMove={e => { if (dragging) place(e); }} onPointerUp={() => setDragging(false)} onPointerCancel={() => setDragging(false)}>
      <image href={photo.url} width="1000" height={height}/>
      {a && b && <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="white" strokeWidth="4" strokeDasharray="12 8"/>}
      {[a, b].map((p, i) => p && <g key={i} pointerEvents="none"><line x1={p.x - nx} y1={p.y - ny} x2={p.x + nx} y2={p.y + ny} stroke={i === 0 ? '#b6f176' : '#ffe0a3'} strokeWidth="5"/><circle cx={p.x} cy={p.y} r="12" fill="white"/><text x={Math.min(p.x + 18, 860)} y={Math.max(p.y - 18, 35)} fontSize="30" fill="white" stroke="#183729" strokeWidth="3" paintOrder="stroke">{i === 0 ? 'Start' : 'Tip'}</text></g>)}
    </svg>
    <fieldset className="coordinate-fields"><legend>Side fine adjustment (% of photo)</legend>
      {(['start', 'tip'] as const).map(key => <div key={key}><strong>{key === 'start' ? 'Start' : 'Tip'}</strong>{(['x', 'y'] as const).map(axis => {
        const point = key === 'start' ? start : tip;
        return <label key={axis}>{axis.toUpperCase()}<input aria-label={`Side ${key} ${axis} percent`} type="number" min="0" max="100" step="0.1" value={point ? Number((point[axis] * 100).toFixed(1)) : ''}
          onChange={e => { const value = Number(e.target.value); if (e.target.value !== '' && value >= 0 && value <= 100) { const changed = { ...(point ?? { x: .5, y: .5 }), [axis]: value / 100 }; onChange(key === 'start' ? changed : start, key === 'tip' ? changed : tip); } }}/></label>;
      })}</div>)}
    </fieldset>
    <Button variant="ghost" onClick={() => onChange(null, null)}>Clear side markers</Button>
    <p className="field-help">Side markers are optional. Without two distinct markers, the automated side comparison is unavailable; generation can still continue.</p>
  </section>;
}
