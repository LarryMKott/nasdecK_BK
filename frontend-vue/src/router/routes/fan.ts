import type { RouteRecordRaw } from 'vue-router'

/** 风扇控制 */
export default {
  path: '/fan',
  name: 'fan',
  component: () => import('@/views/Fan/index.vue'),
  meta: { label: '风扇控制', icon: 'fan', group: '其他', realtime: true },
} satisfies RouteRecordRaw
