import type { RouteRecordRaw } from 'vue-router'

/** Docker 容器 */
export default {
  path: '/docker',
  name: 'docker',
  component: () => import('@/views/Docker/index.vue'),
  meta: { label: 'Docker', icon: 'docker', group: '其他', realtime: true },
} satisfies RouteRecordRaw
