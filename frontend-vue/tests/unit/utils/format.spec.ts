import { describe, expect, it } from 'vitest'
import { clampPct, fmtLoad, fmtSpeed, tempColor, usageColor } from '@/utils/format'

describe('fmtSpeed', () => {
  it('B/s 区间取整', () => {
    expect(fmtSpeed(0)).toBe('0 B/s')
    expect(fmtSpeed(1023.6)).toBe('1024 B/s')
  })
  it('KB/s 一位小数', () => {
    expect(fmtSpeed(1024)).toBe('1.0 KB/s')
    expect(fmtSpeed(1536)).toBe('1.5 KB/s')
  })
  it('MB/s 两位小数', () => {
    expect(fmtSpeed(1048576)).toBe('1.00 MB/s')
  })
  it('空值/NaN 返回 -', () => {
    expect(fmtSpeed(null)).toBe('-')
    expect(fmtSpeed(undefined)).toBe('-')
    expect(fmtSpeed(Number.NaN)).toBe('-')
  })
})

describe('fmtLoad', () => {
  it('数值字符串两位小数', () => {
    expect(fmtLoad('1.234')).toBe('1.23')
    expect(fmtLoad(2)).toBe('2.00')
  })
  it('非数字原样返回', () => {
    expect(fmtLoad('abc')).toBe('abc')
    expect(fmtLoad('-')).toBe('-')
  })
})

describe('tempColor', () => {
  it('null 返回 muted', () => {
    expect(tempColor(null)).toBe('var(--muted)')
  })
  it('阈值分档（默认 trip=60）', () => {
    expect(tempColor(30)).toBe('var(--green)')
    expect(tempColor(48)).toBe('var(--orange)') // 48/60 = 0.8 → 橙
    expect(tempColor(58)).toBe('var(--red)') // 58/60 ≈ 0.967 → 红
  })
  it('自定义 trip 参与分档', () => {
    expect(tempColor(80, 100)).toBe('var(--orange)')
    expect(tempColor(95, 100)).toBe('var(--red)')
  })
})

describe('usageColor', () => {
  it('默认 70/85 分档', () => {
    expect(usageColor(50)).toBe('var(--green)')
    expect(usageColor(75)).toBe('var(--orange)')
    expect(usageColor(90)).toBe('var(--red)')
  })
  it('自定义阈值', () => {
    expect(usageColor(60, 90, 50)).toBe('var(--orange)')
  })
})

describe('clampPct', () => {
  it('夹到 [0,100]', () => {
    expect(clampPct('-5')).toBe(0)
    expect(clampPct('42.7')).toBe(42.7)
    expect(clampPct('150')).toBe(100)
  })
  it('非数字返回 null', () => {
    expect(clampPct('abc')).toBeNull()
    expect(clampPct(undefined)).toBeNull()
  })
})
