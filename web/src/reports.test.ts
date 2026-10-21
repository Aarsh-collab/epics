import { expect, it } from 'vitest';
import { parseReport, scalerStatuses, qaStatuses } from './reports';
it('preserves every scaler status and the original operator message', () => {
  for (const status of scalerStatuses) expect(parseReport({ status, message: 'Operator explanation', warnings: ['Inspect base'] })).toMatchObject({ source: 'scaler', status, message: 'Operator explanation', warnings: ['Inspect base'] });
});
it('preserves independent QA statuses and unverified checks', () => {
  for (const overall_status of qaStatuses) expect(parseReport({ overall_status, summary: 'QA explanation', not_verified: ['Side photo'] })).toMatchObject({ source: 'qa', status: overall_status, notVerified: ['Side photo'] });
});
it('rejects malformed, ambiguous, and unknown reports', () => {
  for (const value of [null, [], {}, { status: 'pass', message: 'Wrong source' }, { status: 'fit_failed' }, { status: 'fit_failed', overall_status: 'fail', message: 'Ambiguous' }]) expect(() => parseReport(value)).toThrow();
});
