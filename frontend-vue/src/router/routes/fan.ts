import type { RouteRecordRaw } from 'vue-router'
import Fan from '@/views/Fan/index.vue'

/** 风扇控制 */
export default {
  path: '/fan',
  name: 'fan',
  component: Fan,
  meta: { label: '风扇控制', icon: 'fan', group: '其他', realtime: true },
} satisfies RouteRecordRaw
