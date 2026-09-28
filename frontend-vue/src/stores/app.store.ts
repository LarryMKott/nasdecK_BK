/**
 * 全局应用状态（Pinia）—— 主题、侧边栏开关、数据新鲜度中转。
 *
 * 归属划定（与方案 3.2 一致，只收真正全局的）：
 * - 主题（saved/applied）：壳 + 每页 hero 控件共用；
 * - sidebarOpen：移动端抽屉状态，壳独有但由 store 持有便于整洁；
 * - freshness：由 api/client 在每次请求后写入，壳层页眉读取展示（store 只转手，不复制状态）。
 * 页面私有数据（各页 ref 的轮询结果）不进 store，留在各自 business composable。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { freshness } from '@/api/client'
import { THEME_KEY, THEME_ZH, nextTheme, resolveTheme } from '@/utils/theme'
import type { ThemeApplied, ThemeSaved } from '@/utils/theme'

export const useAppStore = defineStore('app', () => {
  // ===== 主题 =====
  const themeSaved = ref<ThemeSaved>('auto')
  const themeApplied = ref<ThemeApplied>('light')
  /** init 只做一次（随 store 实例走，而非模块级单例——多 pinia 实例/测试环境可各自初始化） */
  const themeInited = ref(false)

  function syncTheme(): void {
    const s = localStorage.getItem(THEME_KEY)
    themeSaved.value = s === 'dark' || s === 'light' || s === 'auto' ? s : 'auto'
    const t = resolveTheme()
    themeApplied.value = t
    document.documentElement.setAttribute('data-theme', t)
  }

  /** 壳挂载时调用一次：把 data-theme 落到 <html>，不必等 hero 控件渲染 */
  function initTheme(): void {
    if (!themeInited.value) {
      themeInited.value = true
      syncTheme()
    }
  }

  function cycleTheme(): void {
    themeSaved.value = nextTheme(themeSaved.value)
    localStorage.setItem(THEME_KEY, themeSaved.value)
    syncTheme()
  }

  const themeLabel = computed(() => THEME_ZH[themeSaved.value] ?? themeSaved.value)

  // ===== 侧边栏（移动端抽屉） =====
  const sidebarOpen = ref(false)
  function toggleSidebar(): void {
    sidebarOpen.value = !sidebarOpen.value
  }
  function closeSidebar(): void {
    sidebarOpen.value = false
  }

  return {
    // 新鲜度只转手：壳层按引用读，api 层持续写入
    freshness,
    themeSaved,
    themeApplied,
    themeLabel,
    sidebarOpen,
    initTheme,
    syncTheme,
    cycleTheme,
    toggleSidebar,
    closeSidebar,
  }
})
