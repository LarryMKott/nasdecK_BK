/**
 * 路由模块汇总 + 侧栏导航构建。
 *
 * 侧栏 NAV 不再是 App.vue 里的手写数组，而是从各模块路由的 meta 归一而来：
 * label / icon / group / realtime 单一数据源在路由文件里，加页面 = 加一个路由文件。
 *
 * 说明：组件一律 () => import() 懒加载 —— 产物为多文件形态后（见 vite.config.ts 注释），
 * 每个视图各自成 chunk，首屏只拉当前页 + vendor，产物单文件不再随页面数增长；
 * 首屏落点 /detect 由路由 '/' redirect 保证，懒加载不改变入口行为。
 */
import type { RouteRecordRaw } from 'vue-router'
import type { NavBlock } from '@/types/common.types'
import detect from './detect'
import system from './system'
import temperature from './temperature'
import history from './history'
import disks from './disks'
import storage from './storage'
import fan from './fan'
import docker from './docker'
import ports from './ports'
import automation from './automation'
import manual from './manual'
import about from './about'

/** 顺序即侧栏顺序：检测 → 资源监控 → 存储 → 其他 */
const moduleRoutes: RouteRecordRaw[] = [
  detect,
  system,
  temperature,
  history,
  disks,
  storage,
  fan,
  docker,
  ports,
  automation,
  manual,
  about,
]
export default moduleRoutes

/** 由路由 meta 归一出侧栏结构（相邻同 group 合并为一个分组块） */
export function buildNav(routes: RouteRecordRaw[]): NavBlock[] {
  const blocks: NavBlock[] = []
  for (const r of routes) {
    const m = (r.meta ?? {}) as { label?: string; icon?: string; group?: string; realtime?: boolean }
    const group = m.group ?? ''
    let block = blocks.find(b => (b.group ?? '') === group)
    if (!block) {
      block = group ? { group, tabs: [] } : { tabs: [] }
      blocks.push(block)
    }
    block.tabs.push({
      id: String(r.name ?? ''),
      label: m.label ?? String(r.name ?? ''),
      icon: m.icon ?? '',
      realtime: m.realtime === true,
    })
  }
  return blocks
}

/** 壳层侧栏导航（模块加载期构建一次） */
export const NAV: NavBlock[] = buildNav(moduleRoutes)
