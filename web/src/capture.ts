export type Point = { x: number; y: number };
export const fractions = [0.25, 0.5, 0.75] as const;
export function station(start: Point, end: Point, fraction: number): Point { return { x: start.x + (end.x - start.x) * fraction, y: start.y + (end.y - start.y) * fraction }; }
export function validMm(value: string) { return value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) > 0 && Number(value) <= 2000; }
export function landmarkDistance(start: Point, end: Point) { return Math.hypot(end.x - start.x, end.y - start.y); }

// Same rule as epics_geometry.measurements._check_consistent: a closed convex section of max width W has 2W < C <= pi*W.
export function sectionProblem(circumference: string, width: string): string | null {
  if (!validMm(circumference) || !validMm(width)) return null;
  const c = Number(circumference), w = Number(width);
  if (c > Math.PI * w + Math.max(0.5, 0.01 * c)) return `Circumference is more than π × width (${(Math.PI * w).toFixed(1)} mm). Re-check both.`;
  if (c <= 2 * w) return `Circumference must be more than twice the width (${(2 * w).toFixed(1)} mm). Re-check both.`;
  return null;
}

export type StubMeasurements = { schema_version: 1; units: 'mm'; length_mm: number; sections: { fraction: number; circumference_mm: number; max_width_mm: number }[] };

// The scaler's input (schemas/stub_measurements.v1.schema.json), or null when any station is missing or inconsistent.
export function stubMeasurements(length: string, circumferences: string[], widths: string[], unreliable: boolean[]): StubMeasurements | null {
  if (!validMm(length) || unreliable.some(Boolean)) return null;
  if (fractions.some((_, i) => !validMm(circumferences[i]) || !validMm(widths[i]) || sectionProblem(circumferences[i], widths[i]))) return null;
  return { schema_version: 1, units: 'mm', length_mm: Number(length), sections: fractions.map((f, i) => ({ fraction: f, circumference_mm: Number(circumferences[i]), max_width_mm: Number(widths[i]) })) };
}
