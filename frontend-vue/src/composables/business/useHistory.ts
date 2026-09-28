/**
 * 历史趋势页业务逻辑：主图 canvas 绘制 + 数据拉取 + 风扇通道历史 + 报告弹窗编排。
 * 报告的统计/文案/导出格式生成是纯函数，在 utils/historyReport（可单测）；
 * 本模块只做数据编排与绘制。口径与旧页 drawHist / loadHistory 完全一致。
 */
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { getFanHistory, getFanLabels, getHistory } from '@/api/modules/history.api'
import type { HistoryRange as Range } from '@/api/modules/history.api'
import type { FanHistoryPoint, FanLabelEntry, HistPoint } from '@/types/api/history.types'
import { pageCacheGet, pageCacheSet } from '@/composables/common/pageCache'
import { fmtSpeed } from '@/utils/format'
import {
  buildOverview,
  computeStats,
  downloadReport,
  fmtTime,
  metricNarrative,
  REPORT_METRICS,
  toCSV,
  toHTML,
  toMarkdown,
} from '@/utils/historyReport'
import type { ReportItem, ReportMetricDef, Stat } from '@/utils/historyReport'

export type Metric = 'disk' | 'net' | 'power' | 'temp' | 'fan' | 'mem'
type RptFmt = 'html' | 'md' | 'csv'

/** 风扇通道历史序列（peArr/pwmArr 与 rpm 逐点对齐，绘制接管状态拐点用） */
export interface FanChannel {
  name: string
  color: string
  rpm: (number | null)[]
  pe: number | null
  pwm: number | null
  peArr: (number | null)[]
  pwmArr: (number | null)[]
}

/* ---------------- 常量 ---------------- */

export const METRIC_TABS: { id: Metric; label: string }[] = [
  { id: 'disk', label: '磁盘 I/O' },
  { id: 'net', label: '网络' },
  { id: 'power', label: '功耗' },
  { id: 'temp', label: '温度' },
  { id: 'fan', label: '风扇' },
  { id: 'mem', label: '内存' },
]
const METRIC_TITLE: Record<Metric, string> = {
  disk: '磁盘 I/O 历史趋势',
  net: '网络速率历史趋势',
  power: 'CPU 功耗历史趋势',
  temp: '温度历史趋势',
  fan: '风扇转速历史趋势',
  mem: '内存占用历史趋势',
}
export const RANGE_TABS: { id: Range; label: string; stat: string }[] = [
  { id: '24h', label: '24小时', stat: '24 小时' },
  { id: '7d', label: '7天', stat: '7 天' },
  { id: '30d', label: '30天', stat: '30 天' },
]
const RANGE_ZH: Record<Range, string> = { '24h': '24 小时', '7d': '7 天', '30d': '30 天' }
export const RPT_RANGES: { id: Range; label: string }[] = RANGE_TABS.map(r => ({ id: r.id, label: r.stat }))
export const RPT_FORMATS: { id: RptFmt; label: string }[] = [
  { id: 'html', label: '单文件 HTML（含图）' },
  { id: 'md', label: 'Markdown' },
  { id: 'csv', label: 'CSV 数据' },
]

/** 主图各维度的字段 / 颜色 / 图例名 / 量纲 */
const CHART_CFG: Record<Metric, { keys: [string, string, string][]; unit: string }> = {
  disk: {
    keys: [
      ['disk_read', '#4aa3ff', '读'],
      ['disk_write', '#ff7a59', '写'],
    ],
    unit: 'speed',
  },
  net: {
    keys: [
      ['net_rx', '#3ec97a', '↓下载'],
      ['net_tx', '#ffb020', '↑上传'],
    ],
    unit: 'speed',
  },
  power: { keys: [['cpu_power', '#b07cff', 'CPU 功耗']], unit: 'w' },
  temp: {
    keys: [
      ['cpu_temp', '#4aa3ff', 'CPU温度'],
      ['mb_temp', '#3ec97a', '主板温度'],
      ['disk_temp_max', '#ff7a59', '硬盘最高温'],
      ['raid_temp', '#b07cff', '阵列卡'],
      ['gpu_temp', '#ffb020', 'GPU温度'],
    ],
    unit: 'c',
  },
  fan: { keys: [['fan_rpm_avg', '#4aa3ff', '风扇平均转速']], unit: 'rpm' },
  mem: { keys: [['mem_used_pct', '#3ec97a', '内存占用率']], unit: 'pct' },
}
const UNIT_TEXT: Record<string, string> = {
  w: '单位 W',
  c: '单位 °C',
  rpm: '单位 RPM',
  pct: '单位 %',
  speed: '速率自适应 B/s·KB/s·MB/s',
}

function cssVar(name: string, fallback = ''): string {
  return (getComputedStyle(document.documentElement).getPropertyValue(name) || '').trim() || fallback
}

export function useHistory() {
  /* ---------------- 主图状态 ---------------- */

  const histMetric = ref<Metric>('disk')
  const histRange = ref<Range>('24h')
  const busy = ref(true)
  /** hero 右上角的刷新时间（之前没传，控件一直显示「加载中…」） */
  const lastUpdate = ref('')
  const canvasEl = ref<HTMLCanvasElement | null>(null)
  const legendHtml = ref('')
  let lastHist: { points?: HistPoint[] } | null = null
  let themeObserver: MutationObserver | null = null
  let resizeTimer = 0

  // ===== 温度红线（用户可设，纯前端 localStorage，不写服务端）=====
  const TEMP_THRESHOLD_KEY = 'nasdeck_temp_threshold'
  function loadTempThreshold(): number {
    try {
      const raw = localStorage.getItem(TEMP_THRESHOLD_KEY)
      if (raw != null) {
        const v = Number(raw)
        if (!isNaN(v)) return Math.max(30, Math.min(120, Math.round(v)))
      }
    } catch {
      /* 忽略 */
    }
    return 75
  }
  const tempThreshold = ref<number>(loadTempThreshold())
  function setTempThreshold(v: number): void {
    const n = Math.max(30, Math.min(120, Math.round(Number(v) || 75)))
    tempThreshold.value = n
    try {
      localStorage.setItem(TEMP_THRESHOLD_KEY, String(n))
    } catch {
      /* 忽略 */
    }
    drawHist(lastHist)
  }

  const heroStats = computed(() => [
    { v: RANGE_ZH[histRange.value], k: '当前区间' },
    { v: '30 天', k: '数据保留' },
  ])
  const sectionTitle = computed(() => METRIC_TITLE[histMetric.value])

  /** 画主图（canvas 手绘，口径与旧页 drawHist 完全一致） */
  function drawHist(d: { points?: HistPoint[] } | null): void {
    lastHist = d
    const cv = canvasEl.value
    if (!cv) return
    if (histMetric.value === 'fan') { drawFanMulti(); return }
    const pts = (d && d.points) || []
    const dpr = window.devicePixelRatio || 1
    const cssW = cv.clientWidth || 600
    const cssH = 170
    cv.width = Math.round(cssW * dpr)
    cv.height = Math.round(cssH * dpr)
    const ctx = cv.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, cssW, cssH)
    const muted = cssVar('--muted', '#8a93a6')

    if (!pts.length) {
      ctx.fillStyle = muted
      ctx.font = '13px sans-serif'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText('暂无数据（需运行一段时间后才会有历史记录）', cssW / 2, cssH / 2)
      legendHtml.value = ''
      return
    }

    const CFG = CHART_CFG[histMetric.value] || { keys: [], unit: 'speed' }
    let maxV = 1
    CFG.keys.forEach(k => pts.forEach(p => { const v = p[k[0]] || 0; if (v > maxV) maxV = v }))
    // 温度图：用户红线可能高于当前读数，若不加会被自动缩放顶出画面 → 抬升上限保它在图内
    if (CFG.unit === 'c') maxV = Math.max(maxV, tempThreshold.value || 0)
    const padL = 46, padR = 10, padT = 10, padB = 20
    const X = (i: number) => padL + (cssW - padL - padR) * (i / ((pts.length - 1) || 1))
    const Y = (v: number) => cssH - padB - (cssH - padB - padT) * (v / maxV)
    const fmtAxis = (v: number) =>
      CFG.unit === 'w' ? Math.round(v) + 'W'
        : CFG.unit === 'c' ? Math.round(v) + '°C'
          : CFG.unit === 'rpm' ? Math.round(v) + ' RPM'
            : CFG.unit === 'pct' ? Math.round(v) + '%'
              : v < 10 ? v.toFixed(1) + ' B/s' /* 刻度专用：亚 10 B/s 留 1 位小数，避免 1/3·maxV 四舍五入后与相邻刻度重名 */
                : fmtSpeed(v)

    ctx.strokeStyle = 'rgba(128,128,128,0.15)'
    ctx.lineWidth = 1
    ctx.fillStyle = muted
    ctx.font = '10px sans-serif'
    ctx.textAlign = 'right'
    ctx.textBaseline = 'middle'
    for (let g = 0; g <= 3; g++) {
      const yy = padT + (cssH - padT - padB) * (g / 3)
      ctx.beginPath()
      ctx.moveTo(padL, yy)
      ctx.lineTo(cssW - padR, yy)
      ctx.stroke()
      ctx.fillText(fmtAxis(maxV * (1 - g / 3)), padL - 4, yy)
    }

    const line = (key: string, color: string) => {
      ctx.strokeStyle = color
      ctx.lineWidth = 1.6
      ctx.beginPath()
      pts.forEach((p, i) => {
        const xx = X(i)
        const yy = Y(p[key] || 0)
        if (i) ctx.lineTo(xx, yy)
        else ctx.moveTo(xx, yy)
      })
      ctx.stroke()
    }
    CFG.keys.forEach(k => line(k[0], k[1]))

    // 用户自定义温度红线（仅在温度量纲显示）：线上方淡红危险区 + 红色虚线 + 标签
    if (CFG.unit === 'c' && tempThreshold.value > 0 && tempThreshold.value <= maxV) {
      const ty = Y(tempThreshold.value)
      ctx.fillStyle = 'rgba(229,72,77,0.08)'
      ctx.fillRect(padL, padT, cssW - padL - padR, ty - padT)
      ctx.save()
      ctx.strokeStyle = 'rgba(229,72,77,0.9)'
      ctx.lineWidth = 1.2
      ctx.setLineDash([5, 4])
      ctx.beginPath()
      ctx.moveTo(padL, ty)
      ctx.lineTo(cssW - padR, ty)
      ctx.stroke()
      ctx.restore()
      ctx.fillStyle = 'rgba(229,72,77,0.95)'
      ctx.font = '10px sans-serif'
      ctx.textAlign = 'right'
      ctx.textBaseline = 'bottom'
      ctx.fillText('红线 ' + Math.round(tempThreshold.value) + '°C', cssW - padR - 2, ty - 2)
    }

    ctx.fillStyle = muted
    ctx.textBaseline = 'alphabetic'
    ctx.textAlign = 'left'
    ctx.fillText(fmtTime(pts[0].ts), padL, cssH - 6)
    ctx.textAlign = 'right'
    ctx.fillText(fmtTime(pts[pts.length - 1].ts), cssW - padR, cssH - 6)

    legendHtml.value =
      CFG.keys.map(k => `${k[2]} <span style="color:${k[1]}">■</span>`).join(' &nbsp; ') +
      ' &nbsp; ' + (UNIT_TEXT[CFG.unit] || UNIT_TEXT.speed) +
      '（数据自部署起累积，自动保留 30 天）'
    if (CFG.unit === 'c') {
      legendHtml.value += ' &nbsp; <span style="color:#e5484d">┄ 红线 ' + Math.round(tempThreshold.value) + '°C</span>'
    }
  }

  function drawFanMulti(): void {
    const cv = canvasEl.value
    if (!cv) return
    const chs = fanChannels.value
    const dpr = window.devicePixelRatio || 1
    const cssW = cv.clientWidth || 600
    const cssH = 170
    cv.width = Math.round(cssW * dpr)
    cv.height = Math.round(cssH * dpr)
    const ctx = cv.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, cssW, cssH)
    const muted = cssVar('--muted', '#8a93a6')
    if (!chs.length || !chs[0].rpm.length) {
      ctx.fillStyle = muted; ctx.font = '13px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      ctx.fillText('暂无风扇通道历史数据（运行一段时间后会自动积累）', cssW / 2, cssH / 2)
      legendHtml.value = ''
      return
    }
    let maxV = 1
    chs.forEach(c => c.rpm.forEach(v => { if (v != null && v > maxV) maxV = v }))
    maxV = Math.max(maxV, 200)
    const padL = 46, padR = 10, padT = 10, padB = 20
    const n = chs[0].rpm.length
    const X = (i: number) => padL + (cssW - padL - padR) * (i / ((n - 1) || 1))
    const Y = (v: number) => cssH - padB - (cssH - padT - padB) * (v / maxV)
    ctx.strokeStyle = 'rgba(128,128,128,0.15)'; ctx.lineWidth = 1; ctx.fillStyle = muted; ctx.font = '10px sans-serif'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'
    for (let g = 0; g <= 3; g++) {
      const yy = padT + (cssH - padT - padB) * (g / 3)
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(cssW - padR, yy); ctx.stroke()
      ctx.fillText(Math.round(maxV * (1 - g / 3)) + ' RPM', padL - 4, yy)
    }
    chs.forEach(c => {
      ctx.strokeStyle = c.color; ctx.lineWidth = 1.6; ctx.beginPath()
      let started = false
      c.rpm.forEach((v, j) => {
        if (v == null) return
        const xx = X(j), yy = Y(v)
        if (!started) { ctx.moveTo(xx, yy); started = true } else ctx.lineTo(xx, yy)
      })
      ctx.stroke()
      // 折线只关键节点标记接管状态：起始点、结束点、状态变化拐点（绿=软件接管 红=交还主板）
      // 中间一段状态不变就只在段首/段尾/整线首尾打点，不密密麻麻铺满
      let firstIdx = -1, lastIdx = -1
      for (let j = 0; j < n; j++) if (c.rpm[j] != null) { if (firstIdx === -1) firstIdx = j; lastIdx = j }
      const dotSet = new Set<number>()
      let prevPe: number | null = null
      for (let j = firstIdx; j <= lastIdx; j++) {
        const v = c.rpm[j]
        if (v == null) continue
        const pe = c.peArr ? c.peArr[j] : null
        if (pe == null) continue
        if (j === firstIdx || j === lastIdx) { dotSet.add(j); prevPe = pe; continue }
        if (prevPe !== null && pe !== prevPe) dotSet.add(j)
        prevPe = pe
      }
      dotSet.forEach((j) => {
        const pe = c.peArr ? c.peArr[j] : null
        const stc = pe === 1 ? '#3ec97a' : pe === 2 ? '#ff5d5d' : '#9aa1ad'
        ctx.fillStyle = stc
        ctx.beginPath(); ctx.arc(X(j), Y(c.rpm[j] as number), 3, 0, Math.PI * 2); ctx.fill()
      })
    })
    ctx.fillStyle = muted; ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'left'
    if (fanHistPoints.value.length) {
      ctx.fillText(fmtTime(fanHistPoints.value[0].ts), padL, cssH - 6)
      ctx.textAlign = 'right'
      ctx.fillText(fmtTime(fanHistPoints.value[fanHistPoints.value.length - 1].ts), cssW - padR, cssH - 6)
    }
    const nameRow = chs.map(c =>
      '<span style="white-space:nowrap;display:inline-flex;align-items:center;gap:5px;font-size:12px;color:var(--text-1);"><span style="color:' + c.color + '">■</span>' + c.name + '</span>'
    ).join('')
    const stRow =
      '<span style="white-space:nowrap;display:inline-flex;align-items:center;gap:5px;"><span style="color:#3ec97a">●</span> 绿色 = 软件接管</span>' +
      '<span style="white-space:nowrap;display:inline-flex;align-items:center;gap:5px;"><span style="color:#ff5d5d">●</span> 红色 = 交还主板</span>'
    legendHtml.value =
      '<div style="display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;margin-bottom:6px;">' + nameRow + '</div>' +
      '<div style="display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;font-size:12px;color:var(--muted);">' + stRow + '</div>'
  }

  async function loadHistory(): Promise<void> {
    // 切页签回来先用缓存把图画出来（秒开），再拉最新数据替换
    const cacheKey = 'hist:' + histRange.value
    if (!lastHist) {
      const cached = pageCacheGet<{ points?: HistPoint[] }>(cacheKey)
      if (cached?.points?.length) {
        busy.value = false
        await nextTick()
        drawHist(cached)
      }
    }
    // 已有图（缓存或上次的）就不转圈：后台静默拉最新
    if (!lastHist) busy.value = true
    try {
      const d = await getHistory(histRange.value)
      await nextTick()
      drawHist(d)
      void loadFanHistory()
      pageCacheSet(cacheKey, d)
      lastUpdate.value = '更新于 ' + new Date().toLocaleTimeString('zh-CN')
    } catch {
      /* 拉取失败保持上一次的图，不打断页面（与旧页一致） */
    } finally {
      busy.value = false
    }
  }

  function pickMetric(m: Metric): void {
    histMetric.value = m
    drawHist(lastHist)
  }
  function pickRange(r: Range): void {
    histRange.value = r
    void loadHistory()
  }

  /* ---------------- 风扇各通道历史（供排查：哪台扇何时被谁接管） ---------------- */

  const fanHistPoints = ref<FanHistoryPoint[]>([])
  const FAN_PALETTE = ['#4aa3ff', '#3ec97a', '#ff7a59', '#b07cff', '#ffb020', '#ff5d8f']
  // 风扇控制里被「隐藏」的通道：拉取标注,按 idx 建集合(hwmon 路径随重启漂移也不影响),
  // 历史图与其保持一致——隐藏的「空口」通道不画曲线。
  const fanLabels = ref<Record<string, FanLabelEntry>>({})
  async function loadFanLabels(): Promise<void> {
    try {
      fanLabels.value = await getFanLabels()
    } catch {
      /* 忽略：不影响风扇历史绘制 */
    }
  }
  const hiddenFanIdx = computed(() => {
    const s = new Set<number>()
    for (const [k, v] of Object.entries(fanLabels.value || {})) {
      if (v && v.hidden) {
        const parts = k.split('::')
        const i = parseInt(parts[parts.length - 1], 10)
        if (!Number.isNaN(i)) s.add(i)
      }
    }
    return s
  })
  // 隐藏通道的「当前名字」也纳入隐藏：历史库里有一批旧样本(idx 字段缺失,但已带自定义名),
  // 仅靠 idx 过滤会漏掉它们,所以同时按名字匹配(名字在所有样本里都稳定)。
  const hiddenFanNames = computed(() => {
    const s = new Set<string>()
    for (const [, v] of Object.entries(fanLabels.value || {})) {
      if (v && v.hidden && v.name) s.add(v.name)
    }
    return s
  })
  async function loadFanHistory(): Promise<void> {
    try {
      await loadFanLabels()
      const d = await getFanHistory(histRange.value)
      fanHistPoints.value = d.points || []
      if (histMetric.value === 'fan') { await nextTick(); drawFanMulti() }
    } catch {
      /* 拉取失败忽略，不影响主图 */
    }
  }
  const fanChannels = computed<FanChannel[]>(() => {
    const pts = fanHistPoints.value
    if (!pts.length) return []
    // 关键：以「通道编号 idx」(物理风扇本身) 为主键分组,显示名直接映射风扇控制里的自定义名。
    // 改名只是换显示名,不该多出一条通道——同 idx 的样本天然合并成一条。
    // 历史库里难免混入「缺 idx 的旧样本」(早期构建写的 / 改名前残留)：它们按名字兜底,
    // 但最终按名字归并回「同名的 idx 组」,避免一台扇裂成两条。隐藏通道(按 idx 或 名字)同样跳过。
    const hidden = hiddenFanIdx.value
    const hiddenNames = hiddenFanNames.value
    const isHiddenFan = (fi: number, n: string): boolean =>
      (!Number.isNaN(fi) && hidden.has(fi)) || hiddenNames.has(n || '')
    const labelNameByIdx = (fi: number): string => {
      for (const [k, v] of Object.entries(fanLabels.value || {})) {
        const parts = k.split('::')
        if (parts.length >= 2 && parseInt(parts[parts.length - 1], 10) === fi) {
          const nm = (v && v.name) || ''
          if (nm) return nm
        }
      }
      return ''
    }
    function toNum(v: unknown): number | null {
      if (v == null || v === '') return null
      const n = Number(v)
      return Number.isFinite(n) ? n : null
    }
    const lastName = (names: string[]): string => {
      for (let i = names.length - 1; i >= 0; i--) if (names[i]) return names[i]
      return ''
    }
    type Sample = { idx?: number; n?: string; rpm?: number | null; pe?: number | null; pwm?: number | null }

    // 第一阶段：有 idx 的样本 -> idx 组(物理风扇)
    const idxGroups: { fi: number; names: string[]; dispName: string }[] = []
    const idxIndex: Record<string, number> = {}
    for (const p of pts) for (const f of (p.fans || []) as Sample[]) {
      const fi = Number(f.idx); const n = f.n || '风扇'
      if (isHiddenFan(fi, n)) continue
      if (Number.isNaN(fi)) continue
      const key = 'I:' + fi
      if (!(key in idxIndex)) { idxIndex[key] = idxGroups.length; idxGroups.push({ fi, names: [], dispName: '' }) }
      idxGroups[idxIndex[key]].names.push(n)
    }
    // 解析 idx 组显示名(直接读控制页自定义名,否则样本最新名) + 建立 名字->idx组 映射
    const nameToIdx: Record<string, number> = {}
    idxGroups.forEach((g, gi) => {
      let nm = labelNameByIdx(g.fi) || lastName(g.names)
      g.dispName = nm
      nameToIdx[nm] = gi
      for (const nn of new Set(g.names)) nameToIdx[nn] = gi
    })
    // 健壮性：当前标注名也映射到对应 idx 组,让「改名前残留的缺 idx 旧样本」能归并回正确通道
    for (const [k, v] of Object.entries(fanLabels.value || {})) {
      const parts = k.split('::')
      const fi = parts.length >= 2 ? parseInt(parts[parts.length - 1], 10) : NaN
      if (Number.isNaN(fi)) continue
      const gi = idxIndex['I:' + fi]
      if (gi == null) continue
      const nm = (v && v.name) || ''
      if (nm) nameToIdx[nm] = gi
    }

    // 第二阶段：缺 idx 的样本 -> 按名字归并回同名 idx 组(否则单列,30 天滚动清除)
    const otherGroups: { names: string[] }[] = []
    const otherIndex: Record<string, number> = {}
    for (const p of pts) for (const f of (p.fans || []) as Sample[]) {
      const fi = Number(f.idx); const n = f.n || '风扇'
      if (isHiddenFan(fi, n)) continue
      if (!Number.isNaN(fi)) continue
      const key = 'N:' + n
      if (!(key in otherIndex)) { otherIndex[key] = otherGroups.length; otherGroups.push({ names: [] }) }
      otherGroups[otherIndex[key]].names.push(n)
    }

    // 合并成最终通道：idx 组优先,缺 idx 组按名字并入同名 idx 组
    const finalIndex: Record<string, number> = {}
    const finalOrder: { name: string }[] = []
    const rawToFinal: number[] = new Array(idxGroups.length + otherGroups.length).fill(-1)
    idxGroups.forEach((g, gi) => {
      if (!(g.dispName in finalIndex)) { finalIndex[g.dispName] = finalOrder.length; finalOrder.push({ name: g.dispName }) }
      rawToFinal[gi] = finalIndex[g.dispName]
    })
    otherGroups.forEach((g, oi) => {
      const nm = lastName(g.names)
      const target = nameToIdx[nm]
      const mi = target != null ? rawToFinal[target] : ((nm in finalIndex) ? finalIndex[nm] : (finalIndex[nm] = finalOrder.length, finalOrder.push({ name: nm }), finalIndex[nm]))
      rawToFinal[idxGroups.length + oi] = mi
    })

    const series = finalOrder.map(o => ({
      name: o.name, rpm: [] as (number | null)[], pe: [] as (number | null)[], pwm: [] as (number | null)[],
    }))
    const rawIndexOf = (f: Sample): number => {
      const fi = Number(f.idx); const n = f.n || '风扇'
      if (isHiddenFan(fi, n)) return -1
      const key = Number.isNaN(fi) ? 'N:' + n : 'I:' + fi
      const ri = Number.isNaN(fi) ? otherIndex[key] : idxIndex[key]
      if (ri == null) return -1
      return Number.isNaN(fi) ? idxGroups.length + ri : ri
    }
    for (const p of pts) {
      const seen: Record<number, boolean> = {}
      for (const f of (p.fans || []) as Sample[]) {
        const ri = rawIndexOf(f); if (ri < 0) continue
        const mi = rawToFinal[ri]; if (mi < 0) continue
        if (seen[mi]) continue
        const s = series[mi]
        s.rpm.push(toNum(f.rpm)); s.pe.push(toNum(f.pe)); s.pwm.push(toNum(f.pwm))
        seen[mi] = true
      }
      for (let i = 0; i < series.length; i++) if (!seen[i]) {
        series[i].rpm.push(null); series[i].pe.push(null); series[i].pwm.push(null)
      }
    }
    // 降采样到 ~120 点，避免 3 天 8640 点拖慢渲染
    const step = Math.max(1, Math.ceil(series[0].rpm.length / 120))
    return series.map((s, i) => {
      const rpmDs: (number | null)[] = []
      const peDs: (number | null)[] = []
      const pwmDs: (number | null)[] = []
      for (let k = 0; k < s.rpm.length; k += step) {
        rpmDs.push(s.rpm[k] != null ? (s.rpm[k] as number) : null)
        peDs.push(s.pe[k] != null ? (s.pe[k] as number) : null)
        pwmDs.push(s.pwm[k] != null ? (s.pwm[k] as number) : null)
      }
      if (rpmDs.length === 0 && s.rpm.some(v => v != null)) {
        const first = s.rpm.findIndex(v => v != null)
        rpmDs.push(s.rpm[first] as number)
        peDs.push(s.pe[first] != null ? (s.pe[first] as number) : null)
        pwmDs.push(s.pwm[first] != null ? (s.pwm[first] as number) : null)
      }
      const pe = s.pe.filter(v => v != null).pop() ?? null
      const pwm = s.pwm.filter(v => v != null).pop() ?? null
      return { name: s.name, color: FAN_PALETTE[i % FAN_PALETTE.length], rpm: rpmDs, pe, pwm, peArr: peDs, pwmArr: pwmDs }
    })
  })

  function onResize(): void {
    if (resizeTimer) window.clearTimeout(resizeTimer)
    resizeTimer = window.setTimeout(() => drawHist(lastHist), 150)
  }

  /* ---------------- 报告弹窗 ---------------- */

  const reportOpen = ref(false)
  const rptRange = ref<Range>('7d')
  const rptFmt = ref<RptFmt>('html')
  const reportStatus = ref('')

  const rptHint = computed(() => {
    return {
      html: '<b>HTML</b>：任意环境（双击即开）均显示真折线图，推荐大多数人使用。',
      md: '<b>Markdown</b>：折线图为内嵌图片，需用支持预览的查看器——VS Code 按 <b>Cmd/Ctrl+Shift+V</b> 进「预览」视图才看得到图；代码视图、GitHub 网页、微信里只显示文字，属正常。',
      csv: '<b>CSV</b>：原始数据（已换算 KB/s、中文表头），用 Excel / 表格软件直接打开分析。',
    }[rptFmt.value] || ''
  })

  function closeReport(): void {
    reportOpen.value = false
    reportStatus.value = ''
  }

  async function genReport(range: Range, fmt: RptFmt): Promise<void> {
    reportStatus.value = '生成中…'
    try {
      const d = await getHistory(range, 30000)
      const pts: HistPoint[] = d.points || []
      if (pts.length < 3) {
        alert('历史样本不足（仅 ' + pts.length + ' 条），无法生成有意义的分析。请让 NAS 运行更长时间后再试。')
        reportStatus.value = ''
        return
      }
      const items: ReportItem[] = REPORT_METRICS.map(m => ({ m, s: computeStats(pts, m.key) }))
        .filter((x): x is { m: ReportMetricDef; s: Stat } => !!x.s)
        .map(x => ({ m: x.m, s: x.s, nar: metricNarrative(x.m, x.s) }))
      const overview = buildOverview(items)
      const genAt = new Date().toLocaleString('zh-CN')
      let content = ''
      let mime = ''
      let ext = ''
      let bom = false
      if (fmt === 'csv') {
        content = toCSV(pts)
        mime = 'text/csv'
        ext = 'csv'
        bom = true
      } else if (fmt === 'md') {
        content = toMarkdown(RANGE_ZH[range], genAt, items, overview, pts)
        mime = 'text/markdown'
        ext = 'md'
      } else {
        content = toHTML(RANGE_ZH[range], genAt, items, overview)
        mime = 'text/html'
        ext = 'html'
      }
      const fn = `nasdeck-历史报告-${range}-${new Date().toISOString().slice(0, 10)}.${ext}`
      downloadReport(fn, content, mime, bom)
      reportStatus.value = '已下载：' + fn
      setTimeout(() => {
        if (reportStatus.value.startsWith('已下载')) reportStatus.value = ''
      }, 4000)
    } catch (e) {
      reportStatus.value = '生成失败：' + (e instanceof Error ? e.message : String(e))
    }
  }

  /* ---------------- 生命周期 ---------------- */

  onMounted(async () => {
    await loadHistory()
    // 主题切换重绘（canvas 取 --muted 等主题色，需跟着变）
    themeObserver = new MutationObserver(() => drawHist(lastHist))
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    window.addEventListener('resize', onResize)
  })
  onUnmounted(() => {
    if (themeObserver) themeObserver.disconnect()
    window.removeEventListener('resize', onResize)
    if (resizeTimer) window.clearTimeout(resizeTimer)
  })

  return {
    histMetric,
    histRange,
    busy,
    lastUpdate,
    canvasEl,
    legendHtml,
    tempThreshold,
    heroStats,
    sectionTitle,
    loadHistory,
    pickMetric,
    pickRange,
    setTempThreshold,
    fanChannels,
    reportOpen,
    rptRange,
    rptFmt,
    reportStatus,
    rptHint,
    closeReport,
    genReport,
  }
}
