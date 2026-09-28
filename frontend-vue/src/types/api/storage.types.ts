/** /api/storage 响应 —— RAID 阵列 / 存储卷 / 拓扑 / 卷映射（后端 collectors/storage.py 契约） */

export interface RaidArray {
  name?: string
  state?: string
  level?: string
  size?: string
  disks?: string[]
}

export interface Volume {
  mount?: string
  size?: string | null
  used?: string | null
  avail?: string | null
  pcent?: string | null
  fstype?: string
  cloud?: boolean
  cloud_type?: string
  cloud_name?: string
  cloud_proto?: string
}

/** 卷映射树（v2.3.0 3.6）：fnOS 卷 → md/LVM → 物理盘 */
export interface MapDisk {
  dev: string
  serial?: string
  model?: string
  temp?: number | null
  asleep?: boolean
  slot?: string
  pd_state?: string
}

export interface MapVolume {
  mount: string
  fs?: string
  size?: string | null
  used?: string | null
  pcent?: string | null
  lvm?: string | null
  md?: { name: string; level?: string; state?: string; size?: string } | null
  disks: MapDisk[]
}

export interface VolumeMap {
  volumes: MapVolume[]
  loose: MapDisk[]
}

export interface StorageResp {
  raid_arrays?: RaidArray[]
  volumes?: Volume[]
  topology?: string
  cloud_mounts?: Volume[]
  volume_map?: VolumeMap
}

/** /api/storage 顶层包裹：{ storage?, error? } */
export interface StorageEnvelope {
  storage?: StorageResp
  error?: string
}
