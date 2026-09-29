import type { RouteRecordRaw } from 'vue-router'

/** 系统资源（CPU / 内存 / 负载实时） */
export default {
  path: '/system',
  name: 'system',
  component: () => import('@/views/SystemResources/index.vue'),
  meta: { label: '系统资源', icon: 'system', group: '资源监控', realtime: true },
} satisfies RouteRecordRaw
