import { describe, expect, it } from 'vitest';
import { qualityAllows, type PhotoQuality } from './photoQuality';

describe('photo quality generation gate', () => {
  const report = (status: PhotoQuality['status']): PhotoQuality => ({ version: 'photo_quality.v1', calibration: 'provisional', mode: 'soft', status, review_token: 'token', checks: [], limitations: 'Not a fit check' });
  it('requires a current completed check', () => {
    expect(qualityAllows(null, 'photo-a', true)).toBe(false);
    expect(qualityAllows({ key: 'photo-a', report: report('pass') }, 'photo-b', true)).toBe(false);
    expect(qualityAllows({ key: 'photo-a', report: report('pass') }, 'photo-a', false)).toBe(true);
  });
  it('requires acknowledgement of soft warnings and never overrides strict blocks', () => {
    expect(qualityAllows({ key: 'a', report: report('warn') }, 'a', false)).toBe(false);
    expect(qualityAllows({ key: 'a', report: report('warn') }, 'a', true)).toBe(true);
    expect(qualityAllows({ key: 'a', report: report('block') }, 'a', true)).toBe(false);
  });
});
