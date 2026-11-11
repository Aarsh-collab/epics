import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { SideViewReport, type SideViewResult } from './SideViewReport';
import { SideLandmarks } from './SideLandmarks';

it('labels missing reports unavailable, never passing', () => {
  const html = renderToStaticMarkup(<SideViewReport/>);
  expect(html).toContain('unavailable');
  expect(html).not.toContain('no mismatch flagged');
});

it('shows mismatch metrics, profiles and the warning-only limitation', () => {
  const report: SideViewResult = { status: 'warn', message: 'Review shape.', fractions: [.25, .5, .75],
    mean_relative_difference: .4, max_relative_difference: .5, mesh_width_over_length: [.2, .2, .2],
    photo_profiles: { side: { width_over_length: [.4, .4, .4] } } };
  const html = renderToStaticMarkup(<SideViewReport report={report}/>);
  expect(html).toContain('review needed');
  expect(html).toContain('40.0%'); expect(html).toContain('50.0%');
  expect(html).toContain('Normalized width profiles');
  expect(html).toContain('mesh is unchanged');
});

it('asks for side markers with accessible numeric controls and explains missing markers', () => {
  const html = renderToStaticMarkup(<SideLandmarks photo={{ file: new File([], 'side.png'), url: 'blob:side', width: 500, height: 280 }} start={null} tip={null} onChange={() => {}}/>);
  expect(html).toContain('Side start x percent'); expect(html).toContain('Side tip y percent');
  expect(html).toContain('generation can still continue');
  expect(html).toContain('90°');
});
