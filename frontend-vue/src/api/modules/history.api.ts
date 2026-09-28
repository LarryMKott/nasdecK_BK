/** 历史域接口：/api/history、/api/fan/history、/api/fan/labels（只读） */
import { apiJson } from '../client'
import type { FanHistoryPoint, FanLabelEntry, HistoryResp } from '@/types/api/history.types'

export type HistoryRange = '24h' | '7d' | '30d'

/** 主图历史（30 秒聚合点，自动保留 30 天）。 */
export function getHistory(range: HistoryRange, timeout = 20000): Promise<HistoryResp> {
  return apiJson<HistoryResp>('/api/history', { timeout, query: { range, _: Date.now() } })
}

/** 风扇各通道历史（供排查：哪台扇何时被谁接管）。 */
export function getFanHistory(range: HistoryRange, timeout = 20000): Promise<{ points?: FanHistoryPoint[] }> {
  return apiJson<{ points?: FanHistoryPoint[] }>('/api/fan/history', { timeout, query: { range, _: Date.now() } })
}

/** 风扇通道标注（自定义名 / 隐藏标记），历史图与风扇控制页共用同一份。 */
export function getFanLabels(timeout = 20000): Promise<Record<string, FanLabelEntry>> {
  return apiJson<Record<string, FanLabelEntry>>('/api/fan/labels', { timeout })
}
