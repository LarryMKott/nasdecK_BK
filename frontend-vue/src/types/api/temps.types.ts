/** /api/fan/temps 响应 —— 统一温度快照（温度监控页 / 检测页 / 风扇页共用） */

export interface SensorItem {
  name: string
  value: number
  raw?: string
  max?: number | null
  crit?: number | null
}

export interface TempsGpuItem {
  name?: string
  vendor?: string
  type?: string
  temp?: number | null
}

export interface TempsDiskItem {
  dev: string
  name?: string
  category?: string
  is_system?: boolean
  temp?: number | null
  asleep?: boolean
  no_sleep?: boolean
  is_nvme?: boolean
  nvme_start_temp?: number
  intf?: string
}

/** 阵列卡物理盘（RAID5 等虚拟盘场景下物理盘不暴露 /dev，温度只能从阵列卡 storcli 拿） */
export interface RaidDriveItem {
  slot?: string | number
  model?: string
  serial?: string
  temp?: number | null
  intf?: string
  size?: string
}

export interface TempsResp {
  cpu_temp?: number | null
  mb_temp?: number | null
  disks?: TempsDiskItem[]
  raid_drives?: RaidDriveItem[]
  sensors?: SensorItem[]
  raid_temp?: number | null
  raid_controller_temp?: number | null
  gpus?: TempsGpuItem[]
}

/** 温度墙条目（视图组装用） */
export interface WallEntry {
  name: string
  raw?: string
  value: number
  max?: number | null
  crit?: number | null
}
