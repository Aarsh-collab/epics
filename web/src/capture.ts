export type Point = { x: number; y: number };
export const fractions = [0.25, 0.5, 0.75] as const;
export function station(start: Point, end: Point, fraction: number): Point { return { x: start.x + (end.x - start.x) * fraction, y: start.y + (end.y - start.y) * fraction }; }
export function validMm(value: string) { return value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) > 0 && Number(value) <= 2000; }
export function landmarkDistance(start: Point, end: Point) { return Math.hypot(end.x - start.x, end.y - start.y); }
