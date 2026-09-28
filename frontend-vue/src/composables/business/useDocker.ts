/**
 * Docker 页业务逻辑：容器列表轮询（8s，与旧页同拍）+ 单元格格式化。
 * CPU/内存由后端归一化，前端直接显示；网络列有速率显速率、无速率显累计。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { getDocker } from '@/api/modules/docker.api'
import type { DockerContainer, DockerResp } from '@/types/api/docker.types'
import type { HeroStat } from '@/types/common.types'
import { ApiError } from '@/api/client'
import { pageCacheGet, pageCacheSet } from '@/composables/common/pageCache'
import { fmtSpeed } from '@/utils/format'

const CACHE_KEY = 'docker'

// ===== 单位换算（与旧页 fmtBToSize 完全一致）=====
function fmtBToSize(b: number | null | undefined): string {
  if (b == null || isNaN(b)) return '?'
  if (b >= 1e12) return (b / 1e12).toFixed(1) + 'T'
  if (b >= 1e9) return Math.round(b / 1e9) + 'G'
  if (b >= 1e6) return Math.round(b / 1e6) + 'M'
  return '?'
}

export function useDocker() {
  const docker = ref<DockerResp | null>(null)
  const lastUpdate = ref('')
  const busy = ref(false)
  const error = ref('')

  let timer = 0
  let fetching = false

  // ===== 单元格格式化 =====
  function fmtCpu(c: DockerContainer): string {
    return c.cpu != null ? c.cpu.toFixed(1) + '%' : '—'
  }
  function fmtMem(c: DockerContainer): string {
    return c.mem || 'N/A'
  }
  function netDown(c: DockerContainer): string {
    if (c.net_rx_rate != null) return fmtSpeed(c.net_rx_rate)
    if (c.net_rx != null) return '累计 ' + fmtBToSize(c.net_rx)
    return '—'
  }
  function netUp(c: DockerContainer): string {
    if (c.net_tx_rate != null) return fmtSpeed(c.net_tx_rate)
    if (c.net_tx != null) return '累计 ' + fmtBToSize(c.net_tx)
    return '—'
  }
  function netTitle(c: DockerContainer): string {
    const parts: string[] = []
    if (c.net_rx != null) parts.push('累计下行 ' + fmtBToSize(c.net_rx))
    if (c.net_tx != null) parts.push('上行 ' + fmtBToSize(c.net_tx))
    return parts.join(' / ')
  }

  const containers = computed<DockerContainer[]>(() => docker.value?.containers || [])
  const isOk = computed(() => docker.value?.ok !== false)

  const heroStats = computed<HeroStat[]>(() => {
    const d = docker.value
    const running = d?.running || 0
    const total = d?.total || 0
    return [
      { v: running + '/' + total, k: '运行中' },
      {
        v: isOk.value ? '正常' : '读取失败',
        k: '状态',
        cls: isOk.value ? '' : 'warn',
      },
      { v: String(total), k: '容器总数' },
    ]
  })

  async function load(force = false): Promise<void> {
    if (fetching) return
    fetching = true
    if (force) busy.value = true
    // 切页签回来先上缓存秒开，再拉最新数据替换
    if (!docker.value) {
      const cached = pageCacheGet<DockerResp>(CACHE_KEY)
      if (cached) docker.value = cached
    }
    try {
      const j = await getDocker()
      docker.value = j.docker || null
      pageCacheSet(CACHE_KEY, docker.value)
      error.value = ''
      lastUpdate.value = '更新于 ' + new Date().toLocaleTimeString('zh-CN')
    } catch (e) {
      if (e instanceof ApiError) error.value = 'Docker 接口返回异常（HTTP ' + e.status + '）'
      else error.value = '获取 Docker 数据失败：' + (e instanceof Error ? e.message : String(e))
    } finally {
      fetching = false
      busy.value = false
    }
  }

  function refreshAll(): void {
    void load(true)
  }

  onMounted(() => {
    void load(true)
    // 旧页每 8s 轮询一次 Docker 资源占用
    timer = window.setInterval(() => void load(false), 8000)
  })
  onUnmounted(() => {
    if (timer) window.clearInterval(timer)
  })

  return {
    docker,
    containers,
    isOk,
    heroStats,
    lastUpdate,
    busy,
    error,
    refreshAll,
    fmtCpu,
    fmtMem,
    netDown,
    netUp,
    netTitle,
  }
}
