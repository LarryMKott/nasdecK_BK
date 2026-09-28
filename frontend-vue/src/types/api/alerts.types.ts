/** /api/alerts* /api/report /api/errors/clear 响应 —— 告警与诊断报告（后端 health 域契约） */

export interface AlertEntry {
  title?: string
  detail?: string
  level?: string
}

export interface AlertsResp {
  config?: Record<string, unknown>
  alerts?: AlertEntry[]
  evaluated_at?: string
}

/** POST /api/alerts/config 响应 */
export interface AlertSaveResp {
  ok?: boolean
  error?: string
}

/** POST /api/alerts/test 响应：results 为各渠道发送结果 map */
export interface AlertTestResp {
  ok?: boolean
  results?: Record<string, unknown>
  error?: string
}

/** GET /api/report?format=json 响应（健康报告；运行日志弹窗取 log_tail / error_ring） */
export interface ReportResp {
  log_tail?: string
  error_ring?: ErrorRingEntry[]
  [key: string]: unknown
}

/** 错误历史圈条目（后端内存长期保留，连续重复合并计数） */
export interface ErrorRingEntry {
  ts: string
  text: string
  n: number
}
