/**
 * 系统资源页业务逻辑：
 *  - /api/system 首屏 + 每 30s（含 60s 服务端缓存）：内存明细、运行时长、显卡清单
 *  - /api/metrics 每 1s（纯读缓存）：CPU/内存/网络/显卡/磁盘 I/O
 *  - 60 点折线历史（cpuHist/memHist/netHist/gpuHist）
 * 取值口径与老页面完全一致：OVS 桥去重、核显不回退显存占用、GPU 双线（温度橙 + 显存蓝）。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { getSystem } from '@/api/modules/system.api'
import { getMetrics } from '@/api/modules/metrics.api'
import type { GpuItem, SysInfo } from '@/types/api/system.types'
import type { DiskItem, MetricsResp, NetItem } from '@/types/api/metrics.types'
import type { HeroStat } from '@/types/common.types'
import { pageCacheGet, pageCacheSet } from '@/composables/common/pageCache'
import { clampPct, fmtLoad, fmtSpeed } from '@/utils/format'

const HIST_LEN = 60

// ===== 取值口径（与老页面完全一致）=====
/** 过滤虚拟/重复网口：去掉 ovs-system，且 X 与 X-ovs 同时存在时只留 X-ovs */
function pickNet(src: NetItem[]): NetItem[] {
  const ovsBridges = new Set(
    src.filter(n => /[-.]ovs$/.test(n.name || '')).map(n => (n.name || '').replace(/[-.]ovs$/, '')),
  )
  return src.filter(
    n =>
      n.name !== 'ovs-system' &&
      !ovsBridges.has(n.name || '') &&
      !((n.rx_rate || 0) === 0 && (n.tx_rate || 0) === 0),
  )
}

function primaryGpu(list: GpuItem[]): GpuItem | null {
  if (!list.length) return null
  return list.find(g => g.vendor === '10de' || g.vendor === '1002') || list[0]
}

export function useSystemResources() {
  const sys = ref<SysInfo | null>(null)
  const live = ref<MetricsResp | null>(null)
  const lastUpdate = ref('')
  const busy = ref(false)

  const cpuHist = ref<number[]>([])
  const memHist = ref<number[]>([])
  const netHist = ref<{ rx: number; tx: number }[]>([])
  const gpuHist = ref<{ temp: (number | null)[]; mem: (number | null)[] }[]>([])
  const selectedGpu = ref(0)

  let sysTimer = 0
  let metricTimer = 0
  let sysBusy = false
  let metricBusy = false

  // ===== 派生值 =====
  const netRows = computed<NetItem[]>(() => pickNet(live.value?.net || []))
  const netRx = computed(() => netRows.value.reduce((a, n) => a + (n.rx_rate || 0), 0))
  const netTx = computed(() => netRows.value.reduce((a, n) => a + (n.tx_rate || 0), 0))
  const rxTxt = computed(() => (netRows.value.length ? fmtSpeed(netRx.value) : '—'))
  const txTxt = computed(() => (netRows.value.length ? fmtSpeed(netTx.value) : '—'))

  const cpuPct = computed(() => {
    const l = live.value
    if (l?.cpu_usage != null) return Math.round(parseFloat(String(l.cpu_usage)))
    const s = sys.value
    if (s?.cpu_usage != null) return Math.round(parseFloat(String(s.cpu_usage)))
    return null
  })

  const memPct = computed(() => {
    const l = live.value
    if (l?.mem_percent != null) return Math.round(parseFloat(String(l.mem_percent)))
    const p = sys.value?.memory?.percent
    return p != null ? Math.round(p) : null
  })

  const memInfo = computed(() => sys.value?.memory || {})

  /** 主显卡实时占用率：优先真实 util；核显不回退显存占用（那是整机内存占用率，口径不符） */
  const gpuPct = computed(() => {
    const g = primaryGpu(live.value?.gpu || [])
    if (!g) return null
    if (g.util != null) return Math.round(parseFloat(String(g.util)))
    if ((g.vendor === '10de' || g.vendor === '1002') && g.mem_pct != null) {
      return Math.round(parseFloat(String(g.mem_pct)))
    }
    return null
  })

  const gpuList = computed<GpuItem[]>(() => {
    const liveG = live.value?.gpu || []
    const sysG = sys.value?.gpus || []
    return liveG.length ? liveG : sysG
  })
  const gpuName = computed(() => {
    const g = gpuList.value[selectedGpu.value] || gpuList.value[0]
    return g?.name || g?.type || '显卡'
  })
  const gpuNameFull = computed(() => {
    const g = gpuList.value[selectedGpu.value] || gpuList.value[0]
    return g?.name_full || g?.name || ''
  })
  const gpuLegend = computed(() => gpuList.value.length > 1)

  const diskRows = computed<DiskItem[]>(() => live.value?.diskio || [])

  const heroStats = computed<HeroStat[]>(() => [
    { v: cpuPct.value != null ? String(cpuPct.value) : '—', unit: '%', k: 'CPU 使用率' },
    {
      v: gpuPct.value != null ? String(gpuPct.value) : '—',
      unit: '%',
      k: 'GPU 占用',
      tip: '显卡实时使用率（优先独显，无独显时显示核显）',
    },
    {
      k: '网络',
      cls: 'net',
      html:
        '<div style="display:inline-grid;grid-template-columns:14px auto;gap:2px 6px;align-items:baseline;font-size:22px;font-weight:600;line-height:1.3">' +
        '<span style="color:var(--blue)">↑</span><span style="color:var(--blue)">' +
        txTxt.value +
        '</span>' +
        '<span style="color:var(--green)">↓</span><span style="color:var(--green)">' +
        rxTxt.value +
        '</span></div>',
    },
    { v: memPct.value != null ? String(memPct.value) : '—', unit: '%', k: '内存占用' },
    { v: sys.value?.uptime ? String(sys.value.uptime).replace(/\s+/g, '') : '—', k: '已运行' },
  ])

  const loadText = computed(() => {
    const arr = live.value?.load || sys.value?.load || []
    return arr.map(fmtLoad).join(' / ') || '—'
  })

  // ===== 折线数据 =====
  const cpuLines = computed(() => [{ data: cpuHist.value as (number | null)[], color: 'var(--blue)' }])
  const memLines = computed(() => [{ data: memHist.value as (number | null)[], color: '#a855f7' }])
  const netLines = computed(() => [
    { data: netHist.value.map(p => p.tx), color: 'var(--blue)' },
    { data: netHist.value.map(p => p.rx), color: 'var(--green)' },
  ])
  const gpuLines = computed(() => {
    const h = gpuHist.value[selectedGpu.value]
    if (!h) return [{ data: [] as (number | null)[], color: 'var(--orange)' }]
    return [
      { data: h.mem, color: 'var(--blue)' },
      { data: h.temp, color: 'var(--orange)' },
    ]
  })

  // ===== 拉取 =====
  async function fetchSys(force = false): Promise<void> {
    if (sysBusy && !force) return
    sysBusy = true
    if (force) busy.value = true
    // 切页签回来先上缓存秒开，再拉最新数据替换
    if (!sys.value) {
      const cached = pageCacheGet<SysInfo>('system')
      if (cached) sys.value = cached
    }
    try {
      const j = await getSystem({ timeout: 60000 })
      sys.value = j.system || j
      pageCacheSet('system', sys.value)
    } catch {
      /* 网络抖动忽略，下一轮再试 */
    } finally {
      sysBusy = false
      busy.value = false
    }
  }

  async function fetchMetrics(): Promise<void> {
    if (metricBusy) return
    metricBusy = true
    try {
      const d = await getMetrics()
      live.value = d

      const c = clampPct(d.cpu_usage)
      if (c != null) {
        cpuHist.value.push(c)
        if (cpuHist.value.length > HIST_LEN) cpuHist.value.shift()
      }
      const m = clampPct(d.mem_percent)
      if (m != null) {
        memHist.value.push(m)
        if (memHist.value.length > HIST_LEN) memHist.value.shift()
      }
      if (Array.isArray(d.gpu)) {
        d.gpu.forEach((g, i) => {
          if (!gpuHist.value[i]) gpuHist.value[i] = { temp: [], mem: [] }
          const t = g.temp != null ? Math.min(120, Math.max(0, parseFloat(String(g.temp)) || 0)) : null
          gpuHist.value[i].temp.push(t)
          if (gpuHist.value[i].temp.length > HIST_LEN) gpuHist.value[i].temp.shift()
          const mp =
            g.mem_total != null && g.mem_total > 0
              ? g.mem_pct != null
                ? g.mem_pct
                : Math.round(((g.mem_used || 0) / g.mem_total) * 100)
              : null
          gpuHist.value[i].mem.push(mp)
          if (gpuHist.value[i].mem.length > HIST_LEN) gpuHist.value[i].mem.shift()
        })
      }
      const rows = pickNet(d.net || [])
      netHist.value.push({
        rx: rows.reduce((a, n) => a + (n.rx_rate || 0), 0),
        tx: rows.reduce((a, n) => a + (n.tx_rate || 0), 0),
      })
      if (netHist.value.length > HIST_LEN) netHist.value.shift()

      lastUpdate.value = '更新于 ' + new Date().toLocaleTimeString('zh-CN')
    } catch {
      /* 忽略 */
    } finally {
      metricBusy = false
    }
  }

  async function refreshAll(): Promise<void> {
    await Promise.all([fetchSys(true), fetchMetrics()])
  }

  onMounted(() => {
    void fetchSys(true)
    void fetchMetrics()
    metricTimer = window.setInterval(() => void fetchMetrics(), 1000)
    sysTimer = window.setInterval(() => void fetchSys(), 30000)
  })
  onUnmounted(() => {
    if (metricTimer) window.clearInterval(metricTimer)
    if (sysTimer) window.clearInterval(sysTimer)
  })

  return {
    sys,
    selectedGpu,
    lastUpdate,
    busy,
    cpuPct,
    memPct,
    memInfo,
    gpuList,
    gpuName,
    gpuNameFull,
    gpuLegend,
    diskRows,
    heroStats,
    loadText,
    cpuLines,
    memLines,
    netLines,
    gpuLines,
    rxTxt,
    txTxt,
    refreshAll,
  }
}
