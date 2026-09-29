import type { RouteRecordRaw } from 'vue-router'

/** 关于 nasdeck */
export default {
  path: '/about',
  name: 'about',
  component: () => import('@/views/About/index.vue'),
  meta: { label: '关于 nasdeck', icon: 'about', group: '其他' },
} satisfies RouteRecordRaw
