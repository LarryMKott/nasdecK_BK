/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 前端构建配置 —— 多文件产物（index.html + assets/*）。
 *
 * 历史：阶段 1a 曾用 vite-plugin-singlefile 把 JS/CSS 全内联进一个 index.html，
 * 规避网关反代下的资源路径风险；代价是产物随页面增长无限膨胀（迁移动批2后达 1.8MB），
 * 弱网/断网下一次下载失败就整包重来。现拆回多文件：
 *
 * - base './'：index.html 引用 ./assets/<name>-<hash>.js 相对路径，
 *   经飞牛网关（/cgi/ThirdParty/<pkg>/index.cgi/ 反代到 Flask 根）与
 *   直连（http://<nas>:9800/）两种入口都落在同源同目录，无根路径 404 风险；
 *   index.cgi 把 index.cgi/ 之后的路径原样转发，assets/ 请求由 FastAPI 伺服。
 * - vendor 手动分包：vue/vue-router/pinia 变动频率远低于业务代码，
 *   单独成 chunk 后业务页更新不重新下载依赖。
 * - 路由级 () => import() 懒加载（见 router/routes/index.ts）：每页一个 chunk，
 *   首屏只拉当前页 + vendor，单文件上限不再随页面数增长。
 * - 产物文件名带内容 hash：后端对 assets/ 下发 immutable 长缓存（staticpages.py），
 *   index.html 保持 no-store，发布新版后 hash 变化自然失效。
 */
export default defineConfig({
  plugins: [vue()],
  // @ 别名 → src（与 tsconfig paths 保持一致；架构迁移批1引入）
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  base: './',
  build: {
    // 直接产出到 Flask 的模板目录，构建完即就位
    outDir: '../templates/vue',
    // 只清空 templates/vue 自身，绝不动 templates/ 下其他内容
    emptyOutDir: true,
    // 飞牛 WebView / 旧内核浏览器兜底
    target: 'es2019',
    reportCompressedSize: false,
    rollupOptions: {
      output: {
        // 三方依赖单列 vendor chunk（文件名稳定、变动少）；业务代码按路由懒加载自行分包
        manualChunks: {
          vendor: ['vue', 'vue-router', 'pinia'],
        },
      },
    },
  },
  server: {
    port: 5273,
    // 本地开发时把 API 打到 158，便于快速迭代（阶段 1a 之后按需启用）
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:9800',
        changeOrigin: true
      }
    }
  },
  // Vitest（计划步骤 6：utils / composables 单元测试）；组件无关的纯逻辑用 jsdom 即可
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.spec.ts'],
  }
})
