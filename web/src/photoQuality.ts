export type PhotoQuality = {
  version: string; calibration: string; mode: 'soft' | 'strict'; status: 'pass' | 'warn' | 'block';
  review_token: string;
  checks: { code: string; status: 'pass' | 'warn' | 'unavailable'; message: string }[];
  limitations: string;
};
export type CheckedPhoto = { key: string; report: PhotoQuality };

// The report and acknowledgement must match the current photo, markers and preparation.
export function qualityAllows(checked: CheckedPhoto | null, key: string, acknowledged: boolean): boolean {
  if (!checked || checked.key !== key) return false;
  return checked.report.status === 'pass' || (checked.report.status === 'warn' && acknowledged);
}
