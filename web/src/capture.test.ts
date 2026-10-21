import { describe, expect, it } from 'vitest';
import { station, validMm } from './capture';
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
