import { describe, expect, it } from 'vitest';
import { sectionProblem, station, stubMeasurements, validMm } from './capture';
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
