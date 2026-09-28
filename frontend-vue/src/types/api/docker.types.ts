/** /api/docker 响应 —— 容器列表与资源占用（后端 collectors/dockermod.py 契约） */

export interface DockerContainer {
  name?: string
  image?: string
  running?: boolean
  status?: string
  ports?: string
  cpu?: number | null
  cpu_raw?: number | null
  mem?: string | null
  mem_pct?: number | null
  mem_bytes?: number | null
  net_rx?: number | null
  net_tx?: number | null
  net_rx_rate?: number | null
  net_tx_rate?: number | null
  runtime?: string | null
}

export interface DockerResp {
  running?: number
  total?: number
  containers?: DockerContainer[]
  ok?: boolean
}

/** /api/docker 顶层包裹：{ docker: DockerResp } */
export interface DockerEnvelope {
  docker?: DockerResp | null
}
