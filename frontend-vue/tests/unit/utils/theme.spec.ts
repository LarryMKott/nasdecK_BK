import { beforeEach, describe, expect, it } from 'vitest'
import { THEME_KEY, nextTheme, resolveTheme } from '@/utils/theme'

beforeEach(() => {
  localStorage.clear()
  // 清掉 URL 上的干扰参数
  history.replaceState(null, '', location.pathname)
})

describe('resolveTheme 优先级链', () => {
  it('本地 nasdeck_theme 最优先', () => {
    localStorage.setItem(THEME_KEY, 'dark')
    expect(resolveTheme()).toBe('dark')
    localStorage.setItem(THEME_KEY, 'light')
    expect(resolveTheme()).toBe('light')
  })
  it('无本地值时回退系统偏好（jsdom 无 matchMedia → light）', () => {
    expect(resolveTheme()).toBe('light')
  })
})

describe('nextTheme 循环', () => {
  it('浅色 → 深色 → 跟随系统 → 浅色', () => {
    expect(nextTheme('light')).toBe('dark')
    expect(nextTheme('dark')).toBe('auto')
    expect(nextTheme('auto')).toBe('light')
  })
})
