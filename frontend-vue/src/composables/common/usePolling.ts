/**
 * 轮询封装：挂载即启动、卸载即清除，替代各页手写的 timer 变量三件套。
 *
 * 与旧手写语义的差异点（迁移时逐页核对）：
 * - immediate 默认 false：首拉通常由页面以 force=true 显式触发（要 busy 态），
 *   定时器只做后台静默刷新；
 * - 组件卸载自动 clearInterval，无需 onUnmounted 手工清理。
 */
import { onMounted, onUnmounted } from 'vue'

export function usePolling(cb: () => void | Promise<void>, ms: number, opts: { immediate?: boolean } = {}): void {
  let timer = 0
  const run = (): void => {
    void cb()
  }
  onMounted(() => {
    if (opts.immediate) run()
    timer = window.setInterval(run, ms)
  })
  onUnmounted(() => {
    if (timer) window.clearInterval(timer)
  })
}
