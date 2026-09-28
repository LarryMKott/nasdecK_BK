/** 系统域接口：/api/version、/api/system */
import { apiJson } from '../client'
import type { VersionResp } from '@/types/api/version.types'
import type { SystemResp } from '@/types/api/system.types'

export interface GetVersionOptions {
  /** force=1：绕过服务端版本检查缓存（「检查新版本」按钮用） */
  force?: boolean
  timeout?: number
}

export interface GetSystemOptions {
  /** force=1：绕过 60s 服务端缓存（风扇/系统资源页的立即刷新用） */
  force?: boolean
  timeout?: number
}

/** 当前版本与更新检查。失败抛 ApiError（调用方可静默降级）。 */
export function getVersion(opts: GetVersionOptions = {}): Promise<VersionResp> {
  return apiJson<VersionResp>('/api/version', {
    timeout: opts.timeout ?? 15000,
    query: opts.force ? { force: 1 } : undefined,
  })
}

/** 系统信息快照（fnOS 版本 / 内核 / 传感器 / 内存 / 显卡…）。 */
export function getSystem(opts: GetSystemOptions = {}): Promise<SystemResp> {
  return apiJson<SystemResp>('/api/system', {
    timeout: opts.timeout ?? 30000,
    query: opts.force ? { force: 1 } : undefined,
  })
}
