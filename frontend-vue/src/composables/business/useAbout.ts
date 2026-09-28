/**
 * 关于页业务逻辑：版本信息卡（fnOS 版本 / 内核）+ 更新检查。
 * 全部只读接口；切回页签时先用 pageCache 秒开再后台刷新（与全站行为一致）。
 */
import { onMounted, ref } from 'vue'
import { getVersion, getSystem } from '@/api/modules/system.api'
import { pageCacheGet, pageCacheSet } from '@/composables/common/pageCache'

function esc(s: string): string {
  return s.replace(/[&<>"]/g, c =>
    c === '&' ? '&amp;' : c === '<' ? '&lt;' : c === '>' ? '&gt;' : '&quot;',
  )
}

/** 版本号归一化：保证恰好一个 v 前缀（/api/version 的 current/latest 已带 v，避免拼出 vv2.1.0） */
function normV(x: string): string {
  if (!x) return x
  return x[0] === 'v' || x[0] === 'V' ? x : 'v' + x
}

const CACHE_KEY = 'about'

export function useAbout() {
  const appVersion = ref('')
  const fnosVersion = ref('')
  const kernel = ref('')
  const error = ref('')
  /** hero 右上角的刷新时间（之前没传，控件一直显示「加载中…」） */
  const lastUpdate = ref('')

  // 检查更新状态（本地内联提示，不复用旧页全局 #updateBanner）
  const checking = ref(false)
  const updateMsg = ref('')
  const updateUrl = ref('')
  const updateFound = ref(false)

  async function loadInfo(): Promise<void> {
    error.value = ''
    // 切页签回来先上缓存秒开，再拉最新
    const cached = pageCacheGet<{ appVersion: string; fnosVersion: string; kernel: string }>(CACHE_KEY)
    if (cached) {
      appVersion.value = cached.appVersion
      fnosVersion.value = cached.fnosVersion
      kernel.value = cached.kernel
    }
    try {
      // 当前安装版本（用于 hero 副标题）；取不到不影响主体
      try {
        const vj = await getVersion()
        appVersion.value = vj.current || ''
      } catch {
        /* 版本取不到不影响主体 */
      }

      // 版本信息卡：fnOS 版本（顶层）+ 运行内核（system.kernel）
      const sj = await getSystem()
      fnosVersion.value = sj.fnos_version || ''
      kernel.value = sj.system?.kernel || ''
      lastUpdate.value = '更新于 ' + new Date().toLocaleTimeString('zh-CN')
      pageCacheSet(CACHE_KEY, {
        appVersion: appVersion.value,
        fnosVersion: fnosVersion.value,
        kernel: kernel.value,
      })
    } catch (e) {
      error.value = '关于页加载失败：' + esc(String(e))
    }
  }

  async function checkUpdate(): Promise<void> {
    checking.value = true
    updateMsg.value = ''
    updateFound.value = false
    updateUrl.value = ''
    try {
      const d = await getVersion({ force: true })
      const cur = normV(d.current || appVersion.value)
      if (d.update_available && d.latest) {
        updateFound.value = true
        updateUrl.value = d.url || 'https://github.com/LarryMKott/nasdeck/releases'
        updateMsg.value = '发现新版本 ' + normV(d.latest) + '（当前 ' + cur + '）'
      } else if (d.error) {
        updateMsg.value = '检查更新失败：' + d.error
      } else {
        updateMsg.value = '已是最新版本（' + cur + '）'
      }
    } catch (e) {
      updateMsg.value = '检查更新出错：' + esc(String(e))
    } finally {
      checking.value = false
    }
  }

  onMounted(() => {
    void loadInfo()
  })

  return {
    appVersion,
    fnosVersion,
    kernel,
    error,
    lastUpdate,
    checking,
    updateMsg,
    updateUrl,
    updateFound,
    checkUpdate,
  }
}
