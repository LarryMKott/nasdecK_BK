import type { RouteRecordRaw } from 'vue-router'

/** 操作手册 */
export default {
  path: '/manual',
  name: 'manual',
  component: () => import('@/views/Manual/index.vue'),
  meta: { label: '操作手册', icon: 'manual', group: '其他' },
} satisfies RouteRecordRaw
