/** /api/system 响应 —— 系统信息快照（60s 服务端缓存；?force=1 绕过）。
 *  兼容两种形态：顶层平铺字段（旧口径，SystemResources 的 j.system || j 兜底）
 *  与 system 子对象。字段随各页迁移逐步扩充（Fan 页的 sensors.fans 在此追加）。 */

export interface MemInfo {
  used?: string
  available?: string
  cached?: string
  percent?: number
  total?: string
}

export interface GpuItem {
  name?: string
  name_full?: string
  type?: string
  vendor?: string
  temp?: number | null
  mem_pct?: number | null
  mem_used?: number | null
  mem_total?: number | null
  util?: number | null
}

export interface NicItem {
  name?: string
}

export interface SysInfo {
  cpu_usage?: number | null
  cpu_threads?: number
  load?: (string | number)[]
  memory?: MemInfo
  uptime?: string
  kernel?: string
  nics?: NicItem[]
  gpus?: GpuItem[]
}

export interface SystemResp extends SysInfo {
  fnos_version?: string
  /** 旧口径的嵌套形态：后端部分版本把 SysInfo 放在 system 键下 */
  system?: SysInfo
}
