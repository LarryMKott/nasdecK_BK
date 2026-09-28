import type { RouteRecordRaw } from 'vue-router'
import SystemResources from '@/views/SystemResources/index.vue'

/** 系统资源（CPU / 内存 / 负载实时） */
export default {
  path: '/system',
  name: 'system',
  component: SystemResources,
  meta: { label: '系统资源', icon: 'system', group: '资源监控', realtime: true },
} satisfies RouteRecordRaw
