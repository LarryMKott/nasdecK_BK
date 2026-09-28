/** /api/metrics 响应 —— 秒级实时指标（纯读缓存：CPU/内存/网络/GPU/磁盘 I/O） */

import type { GpuItem } from './system.types'

export interface NetItem {
  name?: string
  rx_rate?: number
  tx_rate?: number
}

export interface DiskItem {
  device?: string
  brand?: string
  model?: string
  size?: string
  standby?: boolean
  busy?: number
  read_rate?: number
  write_rate?: number
}

export interface MetricsResp {
  cpu_usage?: number | null
  mem_percent?: number | null
  load?: (string | number)[]
  net?: NetItem[]
  gpu?: GpuItem[]
  diskio?: DiskItem[]
}
