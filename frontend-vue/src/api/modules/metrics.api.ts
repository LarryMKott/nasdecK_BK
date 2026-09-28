/** 指标域接口：/api/metrics */
import { apiJson } from '../client'
import type { MetricsResp } from '@/types/api/metrics.types'

/** 秒级实时指标（纯读缓存，实测 0.002~0.08s，供 1s 轮询）。 */
export function getMetrics(timeout = 30000): Promise<MetricsResp> {
  return apiJson<MetricsResp>('/api/metrics', { timeout })
}
