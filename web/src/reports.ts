export const scalerStatuses = ['measurements_matched', 'invalid_measurements', 'unsupported_mesh', 'alignment_ambiguous', 'section_unsupported', 'fit_failed', 'export_mismatch'] as const;
export const qaStatuses = ['pass', 'warn', 'fail'] as const;
export type DisplayReport = { source: 'scaler' | 'qa'; status: string; message: string; warnings: string[]; notVerified: string[] };
export function parseReport(input: unknown): DisplayReport {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Report must be a JSON object.');
  const r = input as Record<string, unknown>;
  const scaler = typeof r.status === 'string' && (scalerStatuses as readonly string[]).includes(r.status);
  const qa = typeof r.overall_status === 'string' && (qaStatuses as readonly string[]).includes(r.overall_status);
  if (scaler === qa) throw new Error('Choose one recognized scaler or independent QA report.');
  const message = scaler ? r.message : r.summary;
  if (typeof message !== 'string') throw new Error('Report is missing its operator message or summary.');
  const strings = (value: unknown) => Array.isArray(value) ? value.filter((x): x is string => typeof x === 'string') : [];
  return { source: scaler ? 'scaler' : 'qa', status: String(scaler ? r.status : r.overall_status), message, warnings: strings(r.warnings), notVerified: [...strings(r.not_verified), ...strings(r.limitations)] };
}
