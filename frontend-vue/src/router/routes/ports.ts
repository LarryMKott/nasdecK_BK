import type { RouteRecordRaw } from 'vue-router'

/** 端口占用 */
export default {
  path: '/ports',
  name: 'ports',
  component: () => import('@/views/Ports/index.vue'),
  meta: { label: '端口占用', icon: 'ports', group: '其他', realtime: true },
} satisfies RouteRecordRaw
