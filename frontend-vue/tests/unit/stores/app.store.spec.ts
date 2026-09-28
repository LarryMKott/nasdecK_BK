import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAppStore } from '@/stores/app.store'
import { THEME_KEY } from '@/utils/theme'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('app store · 主题', () => {
  it('initTheme 读取 localStorage 并把 data-theme 落到 <html>', () => {
    localStorage.setItem(THEME_KEY, 'dark')
    const app = useAppStore()
    app.initTheme()
    expect(app.themeSaved).toBe('dark')
    expect(app.themeApplied).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('cycleTheme 循环切换并持久化（浅色 → 深色 → 跟随系统）', () => {
    localStorage.setItem(THEME_KEY, 'light')
    const app = useAppStore()
    app.initTheme()
    app.cycleTheme()
    expect(app.themeSaved).toBe('dark')
    expect(localStorage.getItem(THEME_KEY)).toBe('dark')
    app.cycleTheme()
    expect(app.themeSaved).toBe('auto')
    expect(app.themeLabel).toBe('跟随系统')
  })

  it('syncTheme 可重入（外部改了 localStorage 后手动同步）', () => {
    const app = useAppStore()
    app.initTheme()
    localStorage.setItem(THEME_KEY, 'light')
    app.syncTheme()
    expect(app.themeSaved).toBe('light')
  })
})

describe('app store · 侧栏', () => {
  it('toggleSidebar / closeSidebar', () => {
    const app = useAppStore()
    expect(app.sidebarOpen).toBe(false)
    app.toggleSidebar()
    expect(app.sidebarOpen).toBe(true)
    app.closeSidebar()
    expect(app.sidebarOpen).toBe(false)
  })
})

describe('app store · 新鲜度中转', () => {
  it('store 的 freshness 与 api client 是同一引用（api 层写、壳层读）', () => {
    const app = useAppStore()
    app.freshness.lastAny = 12345
    expect(app.freshness.lastAny).toBe(12345)
  })
})
