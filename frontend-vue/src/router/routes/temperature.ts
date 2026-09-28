import type { RouteRecordRaw } from 'vue-router'
import Temperature from '@/views/Temperature/index.vue'

/** 温度监控 */
export default {
  path: '/temps',
  name: 'temps',
  component: Temperature,
  meta: { label: '温度监控', icon: 'thermo', group: '资源监控', realtime: true },
} satisfies RouteRecordRaw
