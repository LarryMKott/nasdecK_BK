import type { RouteRecordRaw } from 'vue-router'

/** 存储卷 */
export default {
  path: '/storage',
  name: 'storage',
  component: () => import('@/views/Storage/index.vue'),
  meta: { label: '存储卷', icon: 'storage', group: '存储', realtime: true },
} satisfies RouteRecordRaw
