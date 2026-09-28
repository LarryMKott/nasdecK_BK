import type { RouteRecordRaw } from 'vue-router'
import Storage from '@/views/Storage/index.vue'

/** 存储卷 */
export default {
  path: '/storage',
  name: 'storage',
  component: Storage,
  meta: { label: '存储卷', icon: 'storage', group: '存储', realtime: true },
} satisfies RouteRecordRaw
