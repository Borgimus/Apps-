import { describe, expect, it } from 'vitest';
import { cycleFreshness, formatTradingMoney, freshness, pnlClass } from '@/lib/trading-utils';

describe('trading dashboard helpers', () => {
  it('formats valid P&L and refuses unavailable values', () => {
    expect(formatTradingMoney(1234.5)).toBe('$1,234.50');
    expect(formatTradingMoney(-2.25)).toBe('-$2.25');
    expect(formatTradingMoney(null)).toBe('Unavailable');
    expect(formatTradingMoney(Number.NaN)).toBe('Unavailable');
  });

  it('classifies freshness without treating malformed timestamps as live', () => {
    const now = Date.parse('2026-07-16T14:00:00.000Z');
    expect(freshness('2026-07-16T13:59:50.000Z', now)).toBe('fresh');
    expect(freshness('2026-07-16T13:59:30.000Z', now)).toBe('delayed');
    expect(freshness('2026-07-16T13:58:00.000Z', now)).toBe('stale');
    expect(freshness('not-a-date', now)).toBe('unknown');
  });

  it('uses distinct positive and negative P&L tones', () => {
    expect(pnlClass(1)).toContain('emerald');
    expect(pnlClass(-1)).toContain('rose');
    expect(pnlClass(0)).toBe('text-ink');
  });

  it('uses the runner five-minute cadence for cycle freshness', () => {
    expect(cycleFreshness(309)).toBe('fresh');
    expect(cycleFreshness(600)).toBe('delayed');
    expect(cycleFreshness(700)).toBe('stale');
    expect(cycleFreshness(undefined)).toBe('unknown');
  });
});
