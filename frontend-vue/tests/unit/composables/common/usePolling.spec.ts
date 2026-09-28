import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { usePolling } from '@/composables/common/usePolling'

/** 挂一个使用 usePolling 的最小组件，返回 spy 供断言 */
function mountWithPolling(cb: () => void, ms: number, opts?: { immediate?: boolean }) {
  const Comp = defineComponent({
    setup() {
      usePolling(cb, ms, opts)
      return () => h('div')
    },
  })
  return mount(Comp)
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('usePolling', () => {
  it('挂载即启动定时器，按周期触发；卸载即清除', async () => {
    const cb = vi.fn()
    const w = mountWithPolling(cb, 1000)
    await vi.advanceTimersByTimeAsync(3500)
    expect(cb).toHaveBeenCalledTimes(3)
    w.unmount()
    await vi.advanceTimersByTimeAsync(5000)
    expect(cb).toHaveBeenCalledTimes(3) // 卸载后不再触发
  })

  it('immediate: true 时挂载立刻先执行一次', async () => {
    const cb = vi.fn()
    const w = mountWithPolling(cb, 1000, { immediate: true })
    expect(cb).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1000)
    expect(cb).toHaveBeenCalledTimes(2)
    w.unmount()
  })
})
