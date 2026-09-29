import type { RouteRecordRaw } from 'vue-router'

/** 历史趋势（metrics 落库图表） */
export default {
  path: '/sys-hist',
  name: 'sys-hist',
  component: () => import('@/views/History/index.vue'),
  meta: { label: '历史趋势', icon: 'history', group: '资源监控' },
} satisfies RouteRecordRaw
