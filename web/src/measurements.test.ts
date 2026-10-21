import { describe, expect, it } from 'vitest';
import Ajv2020 from 'ajv/dist/2020';
import schema from '../../schemas/stub_measurements.v1.schema.json';
import { measurementErrors, toScalerMeasurements, type MeasurementDraft } from './measurements';
import { writeFileSync, mkdirSync } from 'node:fs';

export const sample: MeasurementDraft = { length: '120', circumferences: ['188', '182', '170'], widths: ['64', '62', '58'], unreliable: [false, false, false] };
describe('scaler export contract', () => {
  it('exports the actual frontend payload against the canonical schema', () => {
    const payload = toScalerMeasurements(sample);
    const validate = new Ajv2020().compile(schema);
    expect(validate(payload), JSON.stringify(validate.errors)).toBe(true);
    expect(payload.sections.map(s => s.fraction)).toEqual([.25, .5, .75]);
    expect(payload).not.toHaveProperty('photos');
    mkdirSync('.test-output', { recursive: true });
    writeFileSync('.test-output/frontend-measurements.json', JSON.stringify(payload, null, 2));
  });
  it('blocks unreliable stations even if cached values are valid', () => {
    expect(() => toScalerMeasurements({ ...sample, unreliable: [false, true, false] })).toThrow(/unreliable/);
  });
  it.each(['', ' ', '0', '-1', 'Infinity', 'NaN'])('blocks invalid widths: %s', width => {
    expect(() => toScalerMeasurements({ ...sample, widths: [width, '62', '58'] })).toThrow();
  });
  it('blocks missing stations and impossible circumference/width pairs', () => {
    expect(() => toScalerMeasurements({ ...sample, widths: [] })).toThrow();
    expect(() => toScalerMeasurements({ ...sample, widths: ['100', '62', '58'] })).toThrow(/twice/);
    expect(() => toScalerMeasurements({ ...sample, widths: ['40', '62', '58'] })).toThrow(/too large/);
  });
  it('uses the Python validator tolerance at the circular upper bound', () => {
    expect(measurementErrors({ ...sample, circumferences: [String(Math.PI * 64 + 1), '182', '170'] })).toEqual([]);
    expect(measurementErrors({ ...sample, length: '2001' })).not.toEqual([]);
  });
});
