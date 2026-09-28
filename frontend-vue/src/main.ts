import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './assets/styles/tokens.css'
import './panel.css'
import { resolveTheme } from './utils/theme'

// 首屏前先定主题，避免闪白（与旧页时代顶部内联脚本的做法一致）
document.documentElement.setAttribute('data-theme', resolveTheme())

// 旧书签兼容：v2.3 时代的 hash 无斜杠（#detect），vue-router 需要的是 #/detect
try {
  const h = location.hash
  if (h && h !== '#' && !h.startsWith('#/')) {
    history.replaceState(null, '', '#' + '/' + h.slice(1))
  }
} catch {
  /* 某些沙箱下 location/history 受限，忽略 */
}

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
