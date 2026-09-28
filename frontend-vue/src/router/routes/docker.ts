import type { RouteRecordRaw } from 'vue-router'
import Docker from '@/views/Docker/index.vue'

/** Docker 容器 */
export default {
  path: '/docker',
  name: 'docker',
  component: Docker,
  meta: { label: 'Docker', icon: 'docker', group: '其他', realtime: true },
} satisfies RouteRecordRaw
