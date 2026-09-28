import type { RouteRecordRaw } from 'vue-router'
import Manual from '@/views/Manual/index.vue'

/** 操作手册 */
export default {
  path: '/manual',
  name: 'manual',
  component: Manual,
  meta: { label: '操作手册', icon: 'manual', group: '其他' },
} satisfies RouteRecordRaw
