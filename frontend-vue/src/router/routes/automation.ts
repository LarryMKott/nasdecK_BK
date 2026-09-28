import type { RouteRecordRaw } from 'vue-router'
import Automation from '@/views/Automation/index.vue'

/** 控制与自动化（告警 / 巡检报告 / 错误日志） */
export default {
  path: '/automation',
  name: 'automation',
  component: Automation,
  meta: { label: '控制与自动化', icon: 'automation', group: '其他', realtime: true },
} satisfies RouteRecordRaw
