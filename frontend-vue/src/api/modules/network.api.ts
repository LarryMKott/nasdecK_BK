/** 网络域接口：/api/ports*、/api/network/wol */
import { apiJson, API_BASE } from '../client'
import type { PortInfo, PortReleaseResp, PortsResp } from '@/types/api/ports.types'

/** 宿主机监听端口 + 占用进程 + 来源归类（应用/容器/系统）。 */
export function getPorts(timeout = 15000): Promise<PortsResp> {
  return apiJson<PortsResp>('/api/ports', { timeout })
}

/** 向占用进程发 SIGTERM 释放端口（后端黑名单拦截系统关键进程；403 体也走 {ok,error} 契约）。 */
export function releasePort(pid: number, timeout = 15000): Promise<PortReleaseResp> {
  return apiJson<PortReleaseResp>('/api/ports/release', { timeout, method: 'POST', body: { pid } })
}

/** 应用图标直链（后端按 appid 从应用安装目录取图；找不到 404 → 前端回退纯文字）。 */
export function portAppIconUrl(app: string): string {
  return API_BASE + '/api/ports/appicon?app=' + encodeURIComponent(app)
}

export interface PortRow {
  port: PortInfo
}

/** WOL 唤醒（检测页网络 Tab 用；Detect 迁移时接入） */
export interface WolResp {
  ok?: boolean
  error?: string
}

export function wakeOnLan(mac: string, timeout = 20000): Promise<WolResp> {
  return apiJson<WolResp>('/api/network/wol', { timeout, method: 'POST', body: { mac } })
}
