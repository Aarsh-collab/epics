import type { PhotoQuality } from '../photoQuality';

const labels: Record<string, string> = { blur: 'Focus', exposure: 'Lighting', resolution: 'Resolution', framing: 'Framing', tip_framing: 'Tip framing', segmentation: 'Background removal', clutter: 'Clutter' };
export function PhotoQualityReport({ report }: { report: PhotoQuality }) {
  return <div aria-live="polite">
    <p className="eyebrow">PHOTO QUALITY · {report.status === 'pass' ? 'NO FLAGS FOUND' : report.status === 'block' ? 'RETAKE REQUIRED' : 'REVIEW NEEDED'}</p>
    <ul className="field-help">{report.checks.map(check => <li key={check.code}>
      <strong>{labels[check.code] ?? check.code}: </strong>{check.status === 'pass' ? 'No flag found.' : `${check.status === 'unavailable' ? 'Not checked. ' : ''}${check.message}`}
    </li>)}</ul>
    <p className="field-help">These checks use provisional thresholds and can miss problems or flag usable photos. They do not verify anatomy or clinical fit.</p>
    {report.status === 'block' && <p className="error">Strict checking is enabled. Go back to replace the photo or adjust its markers, then prepare and check it again. Missing checks must also be completed.</p>}
  </div>;
}
