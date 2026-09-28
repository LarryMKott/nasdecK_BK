/** 告警域接口：/api/alerts*、/api/report、/api/errors/clear */
import { apiJson, apiText } from '../client'
import type { AlertSaveResp, AlertTestResp, AlertsResp, ReportResp } from '@/types/api/alerts.types'

/** 告警配置 + 活动告警（活动告警每 15s 轮询；表单配置只在进页/保存后拉）。 */
export function getAlerts(timeout = 30000): Promise<AlertsResp> {
  return apiJson<AlertsResp>('/api/alerts', { timeout, query: { _: Date.now() } })
}

/** 保存告警配置（POST，后端 @require_admin）。 */
export function saveAlertConfig(cfg: Record<string, unknown>, timeout = 30000): Promise<AlertSaveResp> {
  return apiJson<AlertSaveResp>('/api/alerts/config', { timeout, method: 'POST', body: cfg })
}

/** 发送测试通知（POST；SMTP/Bark 走外网可能较慢，超时给足）。 */
export function testAlertNotify(timeout = 60000): Promise<AlertTestResp> {
  return apiJson<AlertTestResp>('/api/alerts/test', { timeout, method: 'POST', body: {} })
}

/** 健康报告 JSON（运行日志弹窗取 log_tail/error_ring；导出 JSON 亦用）。 */
export function getReportJson(opts: { redact?: boolean; timeout?: number } = {}): Promise<ReportResp> {
  return apiJson<ReportResp>('/api/report', {
    timeout: opts.timeout ?? 60000,
    query: { format: 'json', ...(opts.redact ? { redact: 1 } : {}), _: Date.now() },
  })
}

/** 健康报告 HTML（导出排版报告用，返回文本）。 */
export function getReportHtml(opts: { redact?: boolean; timeout?: number } = {}): Promise<string> {
  return apiText('/api/report', {
    timeout: opts.timeout ?? 60000,
    query: { format: 'html', ...(opts.redact ? { redact: 1 } : {}), _: Date.now() },
  })
}

/** 清除错误历史圈（POST，不可恢复）。 */
export function clearErrors(timeout = 10000): Promise<unknown> {
  return apiJson('/api/errors/clear', { timeout, method: 'POST' })
}
