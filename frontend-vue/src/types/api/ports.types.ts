/** /api/ports 响应 —— 监听端口与占用进程（后端 collectors/ports.py 契约） */

export interface PortInfo {
  proto: string
  local: string
  port: number
  state: string
  pid: number | null
  process: string
  scope: string
  app: string
  appname: string
  icon: string
  iconname: string
  desc: string
  reach: string
  public: boolean
  /** 内核态 socket（内核线程如 nfsd 持有，无进程名/PID；后端已按端口号反查补说明） */
  kernel?: boolean
}

export interface PortsResp {
  ports?: PortInfo[]
  error?: string
}

/** POST /api/ports/release 响应 */
export interface PortReleaseResp {
  ok?: boolean
  error?: string
  blocked?: boolean
  process?: string
}
