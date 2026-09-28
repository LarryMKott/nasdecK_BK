/**
 * 路由实例 —— hash 模式（createWebHashHistory）。
 *
 * 为什么是 hash：产物为单 index.html，经飞牛网关 cgi 反代伺服，
 * history 模式的深层路径会 404；hash 与旧版 `#页签` 行为对齐，刷新不触发服务端请求。
 */
import { createRouter, createWebHashHistory } from 'vue-router'
import moduleRoutes from './routes'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/detect' },
    ...moduleRoutes,
    // 未知路径兜底回首屏（含旧书签里已下线的页签名）
    { path: '/:pathMatch(.*)*', redirect: '/detect' },
  ],
  // 换页回顶部（替代旧壳 select() 里的 window.scrollTo）
  scrollBehavior() {
    return { top: 0, behavior: 'smooth' }
  },
})

export default router
