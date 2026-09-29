import type { RouteRecordRaw } from 'vue-router'

/** 硬件配置检测（整机体检总览，首屏落点） */
export default {
  path: '/detect',
  name: 'detect',
  component: () => import('@/views/Detect/index.vue'),
  meta: { label: '硬件配置检测', icon: 'detect', group: '', realtime: true },
} satisfies RouteRecordRaw
