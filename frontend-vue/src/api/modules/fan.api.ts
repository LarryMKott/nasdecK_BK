/** 风扇域接口：/api/fan/*（控制类端点在风扇页迁移时逐个接入） */
import { apiJson } from '../client'
import type { TempsResp } from '@/types/api/temps.types'

/** 统一温度快照：CPU/主板/硬盘/阵列卡/显卡温度（检测页 5s、温度页 5s、风扇页 5s 轮询）。 */
export function getFanTemps(timeout = 30000): Promise<TempsResp> {
  return apiJson<TempsResp>('/api/fan/temps', { timeout, query: { _: Date.now() } })
}
