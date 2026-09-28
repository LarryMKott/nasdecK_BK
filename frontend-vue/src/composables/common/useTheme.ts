/** 主题组合式函数 —— 薄门面，状态与动作都在 app store（全局单例） */
import { storeToRefs } from 'pinia'
import { useAppStore } from '@/stores/app.store'

export function useTheme() {
  const app = useAppStore()
  app.initTheme()
  const { themeSaved: saved, themeApplied: applied } = storeToRefs(app)
  return {
    saved,
    applied,
    cycle: (): void => app.cycleTheme(),
    label: (): string => app.themeLabel,
    sync: (): void => app.syncTheme(),
  }
}
