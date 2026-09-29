import type { RouteRecordRaw } from 'vue-router'

/** 硬盘 SMART */
export default {
  path: '/disks',
  name: 'disks',
  component: () => import('@/views/Disks/index.vue'),
  meta: { label: '硬盘 SMART', icon: 'hdd', group: '存储', realtime: true },
} satisfies RouteRecordRaw
