import type { RouteRecordRaw } from 'vue-router'
import Disks from '@/views/Disks/index.vue'

/** 硬盘 SMART */
export default {
  path: '/disks',
  name: 'disks',
  component: Disks,
  meta: { label: '硬盘 SMART', icon: 'hdd', group: '存储', realtime: true },
} satisfies RouteRecordRaw
