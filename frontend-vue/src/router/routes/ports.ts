import type { RouteRecordRaw } from 'vue-router'
import Ports from '@/views/Ports/index.vue'

/** 端口占用 */
export default {
  path: '/ports',
  name: 'ports',
  component: Ports,
  meta: { label: '端口占用', icon: 'ports', group: '其他', realtime: true },
} satisfies RouteRecordRaw
