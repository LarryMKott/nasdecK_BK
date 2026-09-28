/** /api/history、/api/fan/history 响应 —— 历史趋势（30 天滚动，30 秒聚合一点） */

/** 历史样本点：ts + 各指标键值（键见 CHART_CFG / REPORT_METRICS） */
export interface HistPoint {
  ts: number
  [key: string]: number | null
}

export interface HistoryResp {
  points?: HistPoint[]
}

/** /api/fan/history 样本点：一刻多通道风扇快照 */
export interface FanHistoryPoint {
  ts: number
  fans: FanSample[]
}

/** fans 数组元素（n=显示名 / idx=物理通道号 / pe=接管状态 1 软件接管 2 交还主板） */
export interface FanSample {
  idx?: number
  n?: string
  rpm?: number | null
  pe?: number | null
  pwm?: number | null
  [key: string]: unknown
}

/** /api/fan/labels：key 为 "hwmon路径::idx"，值为 { name?, hidden? } */
export interface FanLabelEntry {
  name?: string
  hidden?: boolean
}
