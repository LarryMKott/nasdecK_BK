/** Docker 域接口：/api/docker */
import { apiJson } from '../client'
import type { DockerEnvelope } from '@/types/api/docker.types'

/** 容器列表与实时资源占用（后端已按逻辑核数归一化 CPU）。 */
export function getDocker(timeout = 15000): Promise<DockerEnvelope> {
  return apiJson<DockerEnvelope>('/api/docker', { timeout })
}
