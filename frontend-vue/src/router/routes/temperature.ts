import type { RouteRecordRaw } from 'vue-router'

/** 温度监控 */
export default {
  path: '/temps',
  name: 'temps',
  component: () => import('@/views/Temperature/index.vue'),
  meta: { label: '温度监控', icon: 'thermo', group: '资源监控', realtime: true },
} satisfies RouteRecordRaw
