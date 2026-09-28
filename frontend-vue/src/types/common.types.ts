/** 通用类型：导航 / 页签（App 壳与路由层共用） */

/** 侧栏页签定义（由路由 meta 归一而来） */
export interface TabDef {
  id: string
  label: string
  icon: string
  /** 是否实时轮询页：是则在页眉显示「数据于 N 秒前更新」 */
  realtime?: boolean
}

/** 侧栏分组块（group 为空 = 顶部不分组区） */
export interface NavBlock {
  group?: string
  tabs: TabDef[]
}

/** 路由 meta 约定 */
export interface RouteMeta {
  /** 侧栏显示名 */
  label: string
  /** AppIcon 名称（PNG 插画键） */
  icon: string
  /** 侧栏分组名；空串 = 不分组 */
  group?: string
  /** 实时轮询页（影响页眉新鲜度告警是否显示） */
  realtime?: boolean
}

/** PanelHero 指标格条目（原 PanelHero.vue 内联类型，上移到 types 供 composable 组装 stats） */
export interface HeroStat {
  /** 主数值（字符串，已格式化） */
  v?: string
  /** 需要富文本（如网络 ↑↓ 双行）时用它，优先于 v */
  html?: string
  /** 单位（小字） */
  unit?: string
  /** 指标名 */
  k: string
  /** 额外类名，如 'net' */
  cls?: string
  /** 悬浮说明 */
  tip?: string
}
