import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { PhotoQualityReport } from './PhotoQualityReport';
import type { PhotoQuality } from '../photoQuality';

const base: PhotoQuality = { version: 'photo_quality.v1', calibration: 'provisional', mode: 'soft', status: 'warn', review_token: 'test', limitations: '', checks: [
  { code: 'blur', status: 'warn', message: 'Hold the phone still.' },
  { code: 'segmentation', status: 'unavailable', message: 'Prepare the photo first.' },
  { code: 'resolution', status: 'pass', message: 'Upload the full-size original.' },
] };

it('shows retake guidance and missing checks without presenting them as passes', () => {
  const html = renderToStaticMarkup(<PhotoQualityReport report={base}/>);
  expect(html).toContain('Hold the phone still.');
  expect(html).toContain('Not checked. Prepare the photo first.');
  expect(html).toContain('No flag found.');
  expect(html).not.toContain('Upload the full-size original.');
  expect(html).toContain('provisional thresholds');
});

it('explains that strict blocks require correction', () => {
  const html = renderToStaticMarkup(<PhotoQualityReport report={{ ...base, mode: 'strict', status: 'block' }}/>);
  expect(html).toContain('RETAKE REQUIRED');
  expect(html).toContain('replace the photo or adjust its markers');
});
