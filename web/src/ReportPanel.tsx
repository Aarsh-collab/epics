import { useState } from 'react';
import { Badge, buttonVariants, Card, CardContent, CardDescription, CardHeader, CardTitle } from './components/ui';
import { parseReport, scalerStatuses, qaStatuses, type DisplayReport } from './reports';

// Separate report sources deliberately: QA thresholds are not scaler tolerances.
const messages: Record<string, string> = {
  measurements_matched: 'All seven exported measurements are within tolerance. This checks submitted dimensions only.',
  invalid_measurements: 'Missing, malformed, or inconsistent measurements. Recheck the measurement form.',
  unsupported_mesh: 'The mesh is unreadable or outside the supported static GLB format.',
  alignment_ambiguous: 'The base and length direction could not be established reliably.',
  section_unsupported: 'A cross-section has a hole, branches, multiple walls, or unsupported concavity.',
  fit_failed: 'The requested dimensions could not be matched within the permitted deformation limits.',
  export_mismatch: 'The exported file failed verification. No completed mesh should be offered.',
  pass: 'Compared dimensions are within the QA pass threshold. Review assumptions and unverified checks.',
  warn: 'One or more checks need review, or a measurement could not be verified.',
  fail: 'One or more compared dimensions exceed the QA failure threshold.',
};
export function ReportPanel() {
  const [source, setSource] = useState<'scaler' | 'qa'>('scaler');
  const [status, setStatus] = useState<string>('measurements_matched');
  const [report, setReport] = useState<DisplayReport | null>(null);
  const [error, setError] = useState('');
  return <Card className="report-panel"><CardHeader><Badge>EXAMPLE REPORT · NOT YOUR RESULT</Badge><CardTitle>Report display preview</CardTitle><CardDescription>No mesh has been generated or evaluated for this capture. These examples preview operator messages while backend integration is pending.</CardDescription></CardHeader><CardContent>
    <label className="report-select">Report source<select value={source} onChange={e => { const next = e.target.value as 'scaler' | 'qa'; setSource(next); setStatus(next === 'scaler' ? scalerStatuses[0] : qaStatuses[0]); }}><option value="scaler">Scaler</option><option value="qa">Independent QA</option></select></label>
    <label className="report-select">Example status<select value={status} onChange={e => setStatus(e.target.value)}>{(source === 'scaler' ? scalerStatuses : qaStatuses).map(s => <option key={s}>{s}</option>)}</select></label>
    <p role="status"><Badge>{status}</Badge></p><p className="muted">{messages[status]}</p>
    <p className="field-help">Not verified: physical measurement accuracy, anatomical fidelity, side-photo appearance, comfort, or clinical fit. Scaler matching and independent QA are separate checks.</p>
    <label className={buttonVariants({ variant: 'outline' })}>Open existing report JSON<input className="report-file" type="file" accept="application/json,.json" aria-label="Open existing report JSON" onChange={async e => { const file = e.target.files?.[0]; e.target.value = ''; if (!file) return; setReport(null); try { if (file.size > 1024 * 1024) throw new Error('Choose a report smaller than 1 MB.'); setReport(parseReport(JSON.parse(await file.text()))); setError(''); } catch (err) { setError(err instanceof Error ? err.message : 'Report could not be read.'); } }}/></label>
    {error && <p role="alert" className="error">{error}</p>}
    {report && <div className="loaded-report"><Badge>IMPORTED REPORT · {report.source}</Badge><h3>{report.status}</h3><p>{report.message}</p><ul>{report.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul><h4>Limitations / not verified</h4><ul>{report.notVerified.map((w, i) => <li key={i}>{w}</li>)}</ul><p className="field-help">This imported report has not been linked to this capture or a GLB. Its status does not establish that your current capture has been processed. Clinical fit is not verified.</p></div>}
  </CardContent></Card>;
}
