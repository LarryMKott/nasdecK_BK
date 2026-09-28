/** 存储域接口：/api/storage */
import { apiJson } from '../client'
import type { StorageEnvelope } from '@/types/api/storage.types'

/** RAID 阵列 / 存储卷 / 拓扑 / 卷映射快照。 */
export function getStorage(timeout = 20000): Promise<StorageEnvelope> {
  return apiJson<StorageEnvelope>('/api/storage', { timeout })
}
