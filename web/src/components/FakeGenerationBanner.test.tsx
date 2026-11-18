import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { FakeGenerationBanner } from './FakeGenerationBanner';

it('clearly labels a fake result and stays hidden for real runs', () => {
  expect(renderToStaticMarkup(<FakeGenerationBanner active/>)).toContain('not generated from your photo');
  expect(renderToStaticMarkup(<FakeGenerationBanner active={false}/>)).toBe('');
});
