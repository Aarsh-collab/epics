import { fractions } from './capture';

export type MeasurementDraft = { length: string; circumferences: string[]; widths: string[]; unreliable: boolean[] };
export type ScalerMeasurements = { schema_version: 1; units: 'mm'; length_mm: number; sections: { fraction: number; circumference_mm: number; max_width_mm: number }[] };
const positive = (value: string | undefined) => value !== undefined && value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) > 0;
export function measurementErrors(draft: MeasurementDraft): string[] {
  const errors: string[] = [];
  if (!positive(draft.length) || Number(draft.length) > 2000) errors.push('Length must be a positive value up to 2,000 mm.');
  fractions.forEach((fraction, i) => {
    const where = `${fraction * 100}% station`;
    if (draft.unreliable[i] !== false) { errors.push(`${where}: measurement is unreliable or missing; complete scaling is blocked.`); return; }
    const c = draft.circumferences[i], w = draft.widths[i];
    if (!positive(c) || !positive(w)) { errors.push(`${where}: enter a positive circumference and maximum width in mm.`); return; }
    if (Number(c) <= 2 * Number(w)) errors.push(`${where}: circumference must exceed twice the maximum width. Recheck the full tape loop and straight-across width.`);
    if (Number(c) > Math.PI * Number(w) + Math.max(.5, .01 * Number(c))) errors.push(`${where}: circumference is too large for this width. Recheck the widest straight span with calipers or parallel straightedges.`);
  });
  return errors;
}
export function toScalerMeasurements(draft: MeasurementDraft): ScalerMeasurements {
  const errors = measurementErrors(draft);
  if (errors.length) throw new Error(errors.join(' '));
  return { schema_version: 1, units: 'mm', length_mm: Number(draft.length), sections: fractions.map((fraction, i) => ({ fraction, circumference_mm: Number(draft.circumferences[i]), max_width_mm: Number(draft.widths[i]) })) };
}
