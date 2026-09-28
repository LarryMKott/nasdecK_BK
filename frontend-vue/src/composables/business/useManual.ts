/**
 * 操作手册业务逻辑：拉取 /manual?embed=1 片段（文本 HTML，走 apiText）+
 * 当前版本号（hero 副标题与「本版本新增」标红用）。
 *
 * DOM 组装（目录生成 / 标红包裹）留在视图层：composable 通过 onContent
 * 回调在内容就绪后通知视图做 nextTick 后的 DOM 加工。
 */
import { ref } from 'vue'
import { getVersion } from '@/api/modules/system.api'
import { apiText } from '@/api/client'
import { pageCacheGet, pageCacheSet } from '@/composables/common/pageCache'

function esc(s: string): string {
  return s.replace(/[&<>"]/g, c =>
    c === '&' ? '&amp;' : c === '<' ? '&lt;' : c === '>' ? '&gt;' : '&quot;',
  )
}

const CACHE_KEY = 'manual-html'

export interface UseManualOptions {
  /** 内容写入 DOM 后回调（视图在此做目录生成 / 标红，需自行 nextTick） */
  onContent?: () => void
}

export function useManual(opts: UseManualOptions = {}) {
  const busy = ref(true)
  const lastUpdate = ref('')
  const error = ref('')
  const contentHtml = ref('')
  const appVersion = ref('')

  async function loadManual(): Promise<void> {
    error.value = ''
    // 切页签回来先上缓存秒开（手册内容随版本走、基本不变），随后仍拉一次最新
    const cached = pageCacheGet<string>(CACHE_KEY)
    if (cached && !contentHtml.value) {
      contentHtml.value = cached
      busy.value = false
      opts.onContent?.()
    }
    // 有内容就不转圈：后台静默拉最新
    if (!contentHtml.value) busy.value = true
    try {
      // 当前版本：用于 hero 副标题 + 「本版本新增」自动标红
      try {
        const vj = await getVersion()
        // 接口返回的是带 v 前缀的完整版本（如 v2.1.0），统一去前缀，
        // 模板里补 'v' 拼接，避免出现「vv2.1.0」；标红匹配用子串不受影响
        appVersion.value = (vj.current || '').replace(/^v/i, '')
      } catch {
        /* 版本取不到不影响主体 */
      }

      const html = await apiText('/manual', { timeout: 30000, query: { _: Date.now() } })
      contentHtml.value = html
      pageCacheSet(CACHE_KEY, html)
      lastUpdate.value = '更新于 ' + new Date().toLocaleTimeString('zh-CN')
      opts.onContent?.()
    } catch (e) {
      error.value = '操作手册加载失败：' + esc(String(e))
    } finally {
      busy.value = false
    }
  }

  return { busy, lastUpdate, error, contentHtml, appVersion, loadManual }
}
