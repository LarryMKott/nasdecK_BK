/**
 * 存储卷页业务逻辑：快照拉取（不轮询，与旧页一致）+ 卷映射 / 阵列 / 容量表格式化。
 * 纯只读：RAID 卡控制属于「硬件配置检测」页。
 */
import { computed, onMounted, ref } from 'vue'
import { getStorage } from '@/api/modules/storage.api'
import type { RaidArray, StorageResp, Volume, VolumeMap } from '@/types/api/storage.types'
import { ApiError } from '@/api/client'
import { pageCacheGet, pageCacheSet } from '@/composables/common/pageCache'
import type { HeroStat } from '@/types/common.types'

const CACHE_KEY = 'storage'

export function useStorage() {
  const st = ref<StorageResp | null>(null)
  const lastUpdate = ref('')
  const busy = ref(false)
  const error = ref('')

  const raidArrays = computed<RaidArray[]>(() => st.value?.raid_arrays || [])
  const volumes = computed<Volume[]>(() => st.value?.volumes || [])
  const topology = computed(() => st.value?.topology || '')
  const vmap = computed<VolumeMap | null>(() => st.value?.volume_map || null)

  /** 平均使用率只算本地卷：云挂载无使用率（后端置空），计入会把均值拉低成假象 */
  const avgPct = computed(() => {
    const local = volumes.value.filter(v => !v.cloud)
    if (!local.length) return 0
    const sum = local.reduce((a, v) => a + (parseInt(v.pcent || '0', 10) || 0), 0)
    return Math.round(sum / local.length)
  })

  const heroStats = computed<HeroStat[]>(() => {
    const p = avgPct.value
    return [
      { v: String(raidArrays.value.length), k: 'RAID 阵列' },
      { v: String(volumes.value.length), k: '存储卷' },
      { v: p + '%', k: '平均使用率', cls: p > 85 ? 'bad' : p > 70 ? 'warn' : '' },
      { v: topology.value ? '有' : '无', k: '拓扑' },
    ]
  })

  // ===== 表格格式化（与旧页逐字一致）=====
  function raidOk(a: RaidArray): boolean {
    return a.state === 'active'
  }
  function cloudLabel(v: Volume): string {
    return (v.cloud_type || '云盘') + (v.cloud_name ? ' · ' + v.cloud_name : '')
  }
  /** 使用率数字：pcent 形如 "45%" → 45 */
  function pctNum(v: Volume): number {
    const n = parseInt(v.pcent || '', 10)
    return isNaN(n) ? 0 : n
  }
  /** 色条中段阈值配色：>85 红 / >70 橙 / 其余绿（与旧页同一套语义色） */
  function fillColor(v: Volume): string {
    const p = pctNum(v)
    return p > 85 ? 'var(--red)' : p > 70 ? 'var(--orange)' : 'var(--green)'
  }
  /**
   * 智能两态：色条够宽时白字嵌在条内（锚在色条末端）；太窄时数字挪到条右侧按语义色显示，
   * 避免被圆角切掉。数字在条外时去掉色条内边距，否则 0% 也会被 padding 撑出一小块绿色。
   */
  function insideFill(v: Volume): boolean {
    return pctNum(v) >= 25
  }
  function fillStyle(v: Volume): string {
    const p = pctNum(v)
    return `width:${p}%;background:${fillColor(v)}` + (insideFill(v) ? '' : ';padding-right:0')
  }
  function outStyle(v: Volume): string {
    return `left:calc(${pctNum(v)}% + 8px);color:${fillColor(v)}`
  }
  /** 型号太长时截断（弹 hover 由 title 补全），卡内保持一行 */
  function shortModel(m?: string): string {
    const s = (m || '').trim()
    if (!s) return ''
    return s.length > 26 ? s.slice(0, 26) + '…' : s
  }

  /** 点物理盘 → 跳「硬盘 SMART」页并高亮该盘的健康卡（App.vue 监听 nasdeck-nav 换页签） */
  function focusDisk(dev: string): void {
    try {
      sessionStorage.setItem('nasdeck_focus_disk', dev)
    } catch {
      /* 某些环境禁 sessionStorage，忽略（跳转仍在，只是不高亮） */
    }
    window.dispatchEvent(new CustomEvent('nasdeck-nav', { detail: { tab: 'disks' } }))
  }

  async function load(force = false): Promise<void> {
    if (force) busy.value = true
    // 切页签回来先上缓存秒开，再拉最新数据替换
    if (!st.value) {
      const cached = pageCacheGet<StorageResp>(CACHE_KEY)
      if (cached) st.value = cached
    }
    try {
      const j = await getStorage()
      if (j.error) {
        error.value = '存储接口错误：' + j.error
        return
      }
      st.value = j.storage || null
      pageCacheSet(CACHE_KEY, st.value)
      error.value = ''
      lastUpdate.value = '更新于 ' + new Date().toLocaleTimeString('zh-CN')
    } catch (e) {
      if (e instanceof ApiError) error.value = '存储接口返回异常（HTTP ' + e.status + '）'
      else error.value = '获取存储数据失败：' + (e instanceof Error ? e.message : String(e))
    } finally {
      busy.value = false
    }
  }

  function refreshAll(): void {
    void load(true)
  }

  onMounted(() => {
    void load(true)
  })

  return {
    st,
    raidArrays,
    volumes,
    topology,
    vmap,
    heroStats,
    lastUpdate,
    busy,
    error,
    refreshAll,
    raidOk,
    cloudLabel,
    pctNum,
    insideFill,
    fillStyle,
    outStyle,
    shortModel,
    focusDisk,
  }
}
