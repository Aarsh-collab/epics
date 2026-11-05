import { describe, expect, it } from 'vitest';
import { hasMainPhoto, photoEntries, photoRecords, sectionProblem, station, stubMeasurements, validMm, type CapturePhoto } from './capture';

describe('optional side photo handoff', () => {
  const photo = (name: string): CapturePhoto => ({ file: new File(['test'], name, { type: 'image/png' }), url: `blob:${name}`, width: 640, height: 480 });
  const main = photo('main.png'), side = photo('side.png');
  it('allows the main photo alone and exports only its metadata and upload', () => {
    expect(hasMainPhoto([main, null])).toBe(true);
    expect(photoEntries([main, null]).map(entry => entry.photo.file)).toEqual([main.file]);
    expect(photoRecords([main, null])).toEqual([{ role: 'generation', filename: 'main.png', width_px: 640, height_px: 480 }]);
  });
  it('preserves the two-photo workflow and omits a removed side photo', () => {
    expect(photoRecords([main, side]).map(p => p.role)).toEqual(['generation', 'review']);
    expect(photoEntries([main, side]).map(entry => entry.photo.file)).toEqual([main.file, side.file]);
    expect(photoRecords([main, null]).some(p => p.role === 'review')).toBe(false);
  });
  it('does not let the optional side photo stand in for the required main photo', () => {
    expect(hasMainPhoto([null, null])).toBe(false);
    expect(hasMainPhoto([null, side])).toBe(false);
    expect(photoRecords([null, side])[0].role).toBe('review');
  });
});
describe('capture handoff geometry', () => {
  it('places stations from the proximal landmark even when the axis is reversed', () => {
    expect(station({ x: .8, y: .9 }, { x: .2, y: .1 }, .25)).toEqual({ x: .65, y: .7 });
    expect(station({ x: .2, y: .1 }, { x: .8, y: .9 }, .5)).toEqual({ x: .5, y: .5 });
  });
  it('rejects absent, non-finite, and nonpositive physical measurements', () => {
    for (const value of ['', ' ', '0', '-2', 'Infinity', 'NaN', '2001']) expect(validMm(value)).toBe(false);
    expect(validMm('120.5')).toBe(true);
  });
});
describe('scaler handoff', () => {
  it('flags circumference/width pairs no closed section can have', () => {
    expect(sectionProblem('188', '64')).toBeNull();
    expect(sectionProblem('250', '64')).toMatch(/π/);
    expect(sectionProblem('120', '64')).toMatch(/twice/);
    expect(sectionProblem('', '64')).toBeNull();
  });
  it('builds stub_measurements v1 only when every station is complete', () => {
    const c = ['188', '182', '170'], w = ['64', '62', '58'];
    expect(stubMeasurements('120', c, w, [false, false, false])).toEqual({
      schema_version: 1, units: 'mm', length_mm: 120,
      sections: [{ fraction: .25, circumference_mm: 188, max_width_mm: 64 }, { fraction: .5, circumference_mm: 182, max_width_mm: 62 }, { fraction: .75, circumference_mm: 170, max_width_mm: 58 }],
    });
    expect(stubMeasurements('120', c, w, [false, true, false])).toBeNull();
    expect(stubMeasurements('120', c, ['64', '', '58'], [false, false, false])).toBeNull();
    expect(stubMeasurements('120', ['300', '182', '170'], w, [false, false, false])).toBeNull();
  });
});
