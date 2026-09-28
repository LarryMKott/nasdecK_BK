import { describe, expect, it } from 'vitest'
import {
  buildOverview,
  computeStats,
  metricNarrative,
  sparkline,
  toCSV,
} from '@/utils/historyReport'
import type { HistPoint } from '@/types/api/history.types'

const pts: HistPoint[] = [
  { ts: 1726100000000, cpu_temp: 50, disk_read: 1024, fan_rpm_avg: 0 },
  { ts: 1726100030000, cpu_temp: 60, disk_read: 2048, fan_rpm_avg: 0 },
  { ts: 1726100060000, cpu_temp: 80, disk_read: 4096, fan_rpm_avg: 0 },
]

describe('computeStats', () => {
  it('均值/极值/极值时间/趋势', () => {
    const s = computeStats(pts, 'cpu_temp')!
    expect(s.n).toBe(3)
    expect(s.mean).toBeCloseTo(63.333, 1)
    expect(s.min).toBe(50)
    expect(s.max).toBe(80)
    expect(s.maxTs).toBe(1726100060000)
    expect(s.trend).toBe('up')
  })
  it('平稳序列判 flat', () => {
    const flat: HistPoint[] = [
      { ts: 1, v: 10 },
      { ts: 2, v: 10 },
      { ts: 3, v: 10 },
    ]
    expect(computeStats(flat, 'v')!.trend).toBe('flat')
  })
  it('全缺失返回 null', () => {
    expect(computeStats(pts, 'not_exist')).toBeNull()
  })
})

describe('metricNarrative', () => {
  it('风扇均速为 0 → danger 停转告警', () => {
    const s = computeStats(pts, 'fan_rpm_avg')!
    const nar = metricNarrative({ key: 'fan_rpm_avg', label: '风扇平均转速', unit: 'RPM', color: '#000', special: 'fan' }, s)
    expect(nar?.level).toBe('danger')
    expect(nar?.text).toContain('停转')
  })
  it('超危险线的温度 → danger + 处置建议', () => {
    const hot: HistPoint[] = [
      { ts: 1, cpu_temp: 60 },
      { ts: 2, cpu_temp: 88 },
      { ts: 3, cpu_temp: 90 },
    ]
    const s = computeStats(hot, 'cpu_temp')!
    const nar = metricNarrative({ key: 'cpu_temp', label: 'CPU 温度', unit: '°C', color: '#000', warn: 75, danger: 85 }, s)
    expect(nar?.level).toBe('danger')
    expect(nar?.text).toContain('危险线')
  })
})

describe('buildOverview', () => {
  it('有 danger 项则总体结论为 danger', () => {
    const overview = buildOverview([
      {
        m: { key: 'cpu_temp', label: 'CPU 温度', unit: '°C', color: '#000' },
        s: computeStats(pts, 'cpu_temp')!,
        nar: { level: 'danger', text: '超危险线' },
      },
    ])
    expect(overview.level).toBe('danger')
  })
})

describe('sparkline', () => {
  it('样本 <2 返回空串', () => {
    expect(sparkline([], '#000')).toBe('')
    expect(sparkline([1], '#000')).toBe('')
  })
  it('含 null 的序列跳过缺失点（风扇通道列表用）', () => {
    const svg = sparkline([1, null, 3], '#000', 100, 40)
    expect(svg).toContain('<polyline')
    expect(svg).not.toContain('NaN')
  })
})

describe('toCSV', () => {
  it('表头含时间列与全部指标，速率换算 KB/s', () => {
    const csv = toCSV(pts)
    const [head, ...rows] = csv.split('\n')
    expect(head.startsWith('时间,')).toBe(true)
    expect(head).toContain('磁盘读取(KB/s)')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toContain('1.0') // 1024 B/s → 1.0 KB/s
  })
})
