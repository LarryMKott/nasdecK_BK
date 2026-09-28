import type { RouteRecordRaw } from 'vue-router'
import About from '@/views/About/index.vue'

/** 关于 nasdeck */
export default {
  path: '/about',
  name: 'about',
  component: About,
  meta: { label: '关于 nasdeck', icon: 'about', group: '其他' },
} satisfies RouteRecordRaw
