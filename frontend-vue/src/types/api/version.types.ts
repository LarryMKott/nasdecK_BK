/** /api/version 响应 —— 版本与更新检查（后端 version.py 契约） */
export interface VersionResp {
  /** 当前安装版本，如 "v1.6.2" */
  current?: string
  /** 最新发布版本；检查失败为 null */
  latest?: string | null
  update_available?: boolean
  /** 新版本下载页 */
  url?: string | null
  /** 检查失败原因（如 GitHub rate limit）；成功检查为 null */
  error?: string | null
}
