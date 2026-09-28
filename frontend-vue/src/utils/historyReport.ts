/**
 * 历史趋势分析报告 —— 纯函数集（无状态、不访问 store/api）。
 * 自 History 视图抽出（架构迁移批2）：统计 / 文案 / CSV / Markdown / HTML 生成，
 * 供 useHistory 的报告弹窗调用；报告在浏览器本地生成后下载（不经过服务器）。
 */
import { fmtSpeed } from './format'
import type { HistPoint } from '@/types/api/history.types'

export type Level = 'ok' | 'warn' | 'danger'

export interface ReportMetricDef {
  key: string
  label: string
  unit: string
  color: string
  warn?: number
  danger?: number
  optional?: boolean
  special?: string
  info?: boolean
}

export interface Stat {
  n: number
  mean: number
  min: number
  max: number
  maxTs: number | null
  minTs: number | null
  trend: 'up' | 'down' | 'flat'
  values: number[]
}

export interface Narrative {
  level: Level
  text: string
}

export interface ReportItem {
  m: ReportMetricDef
  s: Stat
  nar: Narrative | null
}

export const REPORT_METRICS: ReportMetricDef[] = [
  { key: 'cpu_temp', label: 'CPU 温度', unit: '°C', color: '#4aa3ff', warn: 75, danger: 85 },
  { key: 'mb_temp', label: '主板温度', unit: '°C', color: '#3ec97a', warn: 60, danger: 70 },
  { key: 'gpu_temp', label: 'GPU 温度', unit: '°C', color: '#ffb020', warn: 80, danger: 90, optional: true },
  { key: 'disk_temp_max', label: '硬盘最高温', unit: '°C', color: '#ff7a59', warn: 55, danger: 65 },
  { key: 'raid_temp', label: '阵列卡温度', unit: '°C', color: '#b07cff', warn: 70, danger: 80 },
  { key: 'fan_rpm_avg', label: '风扇平均转速', unit: 'RPM', color: '#4aa3ff', special: 'fan' },
  { key: 'mem_used_pct', label: '内存占用率', unit: '%', color: '#3ec97a', warn: 85, danger: 95 },
  { key: 'cpu_power', label: 'CPU 功耗', unit: 'W', color: '#b07cff', info: true },
  { key: 'net_rx', label: '网络下载', unit: 'speed', color: '#3ec97a', info: true },
  { key: 'net_tx', label: '网络上传', unit: 'speed', color: '#ffb020', info: true },
  { key: 'disk_read', label: '磁盘读取', unit: 'speed', color: '#4aa3ff', info: true },
  { key: 'disk_write', label: '磁盘写入', unit: 'speed', color: '#ff7a59', info: true },
]

export const REPORT_GROUPS: { title: string; keys: string[] }[] = [
  { title: '温度', keys: ['cpu_temp', 'mb_temp', 'gpu_temp', 'disk_temp_max', 'raid_temp'] },
  { title: '散热', keys: ['fan_rpm_avg'] },
  { title: '资源占用', keys: ['mem_used_pct', 'cpu_power'] },
  { title: '网络与磁盘 I/O', keys: ['net_rx', 'net_tx', 'disk_read', 'disk_write'] },
]

/** 短时间（MM/DD HH:mm），主图轴与文案共用 */
export function fmtTime(ts: number): string {
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function fmtTimeFull(ts: number): string {
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function trendText(t: Stat['trend']): string {
  return t === 'up' ? '呈上升趋势' : t === 'down' ? '呈下降趋势' : '基本平稳'
}

export function computeStats(pts: HistPoint[], key: string): Stat | null {
  const vs = pts.map(p => p[key]).filter((v): v is number => typeof v === 'number' && !isNaN(v))
  if (!vs.length) return null
  const n = vs.length
  const mean = vs.reduce((a, b) => a + b, 0) / n
  const min = Math.min(...vs)
  const max = Math.max(...vs)
  let maxTs: number | null = null
  let minTs: number | null = null
  for (const p of pts) {
    if (typeof p[key] === 'number') {
      if (maxTs === null && p[key] === max) maxTs = p.ts
      if (minTs === null && p[key] === min) minTs = p.ts
    }
  }
  const h = Math.max(1, Math.floor(n / 2))
  const fmean = vs.slice(0, h).reduce((a, b) => a + b, 0) / h
  const smean = vs.slice(h).reduce((a, b) => a + b, 0) / Math.max(1, n - h)
  let trend: Stat['trend'] = 'flat'
  const diff = mean ? (smean - fmean) / mean : 0
  if (diff > 0.1) trend = 'up'
  else if (diff < -0.1) trend = 'down'
  return { n, mean, min, max, maxTs, minTs, trend, values: vs }
}

export function metricNarrative(m: ReportMetricDef, s: Stat | null): Narrative | null {
  if (!s) return null
  const u = m.unit === '°C' ? '°C' : m.unit === 'RPM' ? ' RPM' : m.unit === '%' ? '%' : m.unit === 'W' ? 'W' : ''
  const fm = (v: number) => (m.unit === 'speed' ? fmtSpeed(v) : String(Math.round(v * 10) / 10))
  const tMax = s.maxTs ? fmtTime(s.maxTs) : '-'
  const tr = trendText(s.trend)
  if (m.special === 'fan') {
    if (s.mean < 1) {
      return { level: 'danger', text: `${m.label}约为 0（或数据缺失），疑似风扇停转，请立即检查风扇接线与供电！` }
    }
    return {
      level: 'ok',
      text: `${m.label}平均 ${Math.round(s.mean)} RPM，最高 ${Math.round(s.max)} RPM（${tMax}），最低 ${Math.round(s.min)} RPM，运转平稳。`,
    }
  }
  if (m.info) {
    return { level: 'ok', text: `${m.label}平均 ${fm(s.mean)}，峰值 ${fm(s.max)}（${tMax}）${tr}。` }
  }
  let level: Level = 'ok'
  let extra = ''
  if (m.danger && s.max >= m.danger) {
    level = 'danger'
    extra = `最高 ${fm(s.max)}${u} 已超过 ${m.danger}${u} 危险线，建议尽快处理（清灰 / 改善风道 / 降负载）。`
  } else if (m.warn && s.max >= m.warn) {
    level = 'warn'
    extra = `最高 ${fm(s.max)}${u} 超过 ${m.warn}${u} 关注线，建议关注散热情况。`
  }
  const head = `${m.label}平均 ${fm(s.mean)}${u}，最高 ${fm(s.max)}${u}（${tMax}），最低 ${fm(s.min)}${u}，${tr}`
  const tail = level === 'ok' ? '，处于正常范围内。' : '，' + extra
  return { level, text: head + tail }
}

export function buildOverview(items: ReportItem[]): Narrative {
  const dangers = items.filter(x => x.nar && x.nar.level === 'danger')
  const warns = items.filter(x => x.nar && x.nar.level === 'warn')
  if (dangers.length) {
    return { level: 'danger', text: `检测到 ${dangers.length} 项危险指标（${dangers.map(x => x.m.label).join('、')}），建议尽快排查散热与负载。` }
  }
  if (warns.length) {
    return { level: 'warn', text: `整体运行基本正常，但有 ${warns.length} 项指标偏高（${warns.map(x => x.m.label).join('、')}），建议关注。` }
  }
  return { level: 'ok', text: '过去区间内各项指标均处于正常范围，运行健康，无紧急风险。' }
}

/** SVG 迷你折线；接受含 null 的序列（缺样本的点跳过，风扇通道列表用） */
export function sparkline(values: (number | null)[], color: string, w?: number, h?: number): string {
  const W = w || 240, H = h || 42
  const vals = (values || []).filter((v): v is number => v != null)
  if (vals.length < 2) return ''
  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const span = max - min || 1
  const X = (i: number) => (i / (vals.length - 1)) * W
  const Y = (v: number) => H - 2 - ((v - min) / span) * (H - 4)
  const pts = vals.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" preserveAspectRatio="none" style="display:block"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round"/></svg>`
}

/** 文本迷你折线（ASCII sparkline）：任何纯文本环境都能看到趋势形状 */
export function asciiSpark(values: number[], w?: number): string {
  const W = w || 32
  if (!values || values.length < 2) return ''
  const bars = '▁▂▃▄▅▆▇█'
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const n = values.length
  let s = ''
  for (let i = 0; i < W; i++) {
    const idx = Math.floor((i / W) * (n - 1))
    const v = values[idx]
    s += bars[Math.min(bars.length - 1, Math.round(((v - min) / span) * (bars.length - 1)))]
  }
  return s
}

export function toCSV(points: HistPoint[]): string {
  const defs: { key: string; label: string; speed?: boolean }[] = [
    { key: 'disk_read', label: '磁盘读取(KB/s)', speed: true },
    { key: 'disk_write', label: '磁盘写入(KB/s)', speed: true },
    { key: 'net_rx', label: '网络下载(KB/s)', speed: true },
    { key: 'net_tx', label: '网络上传(KB/s)', speed: true },
    { key: 'cpu_power', label: 'CPU功耗(W)' },
    { key: 'cpu_temp', label: 'CPU温度(°C)' },
    { key: 'mb_temp', label: '主板温度(°C)' },
    { key: 'gpu_temp', label: 'GPU温度(°C)' },
    { key: 'disk_temp_max', label: '硬盘最高温(°C)' },
    { key: 'raid_temp', label: '阵列卡温度(°C)' },
    { key: 'fan_rpm_avg', label: '风扇转速(RPM)' },
    { key: 'mem_used_pct', label: '内存占用(%)' },
  ]
  const head = '时间,' + defs.map(d => d.label).join(',')
  const rows = points.map(
    p =>
      fmtTimeFull(p.ts) +
      ',' +
      defs
        .map(d => {
          const v = p[d.key]
          if (v == null || isNaN(v)) return ''
          if (d.speed) return (v / 1024).toFixed(1)
          return String(Math.round(v * 10) / 10)
        })
        .join(','),
  )
  return head + '\n' + rows.join('\n')
}

/**
 * 真折线图：内嵌为独立 SVG 图片（base64 data-uri）。
 * VS Code / Typora / Obsidian 等查看器都会渲染为真实折线图，与 HTML 版视觉一致；
 * 不依赖 Mermaid 引擎，避免 VS Code 预览渲染不出 xychart-beta 的问题。
 */
function svgChartImg(m: ReportMetricDef, pairs: { ts: number; v: number }[]): string {
  if (!pairs || pairs.length < 2) return ''
  const conv = m.unit === 'speed' ? (v: number) => v / 1024 : (v: number) => v
  const unitLabel = m.unit === 'speed' ? 'KB/s' : m.unit
  const all = pairs.map(p => ({ t: p.ts, v: conv(p.v) }))
  const step = Math.max(1, Math.ceil(all.length / 60))
  const ds = all.filter((_, i) => i % step === 0 || i === all.length - 1)
  if (ds.length < 2) return ''
  const W = 560, H = 132, padL = 10, padR = 10, padT = 24, padB = 18
  const vals = ds.map(d => d.v)
  const mn = Math.min(...vals)
  const mx = Math.max(...vals)
  const span = mx - mn || 1
  const lo = mn - span * 0.12
  const hi = mx + span * 0.12
  const X = (i: number) => padL + (i / (ds.length - 1)) * (W - padL - padR)
  const Y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB)
  const pts = ds.map((d, i) => `${X(i).toFixed(1)},${Y(d.v).toFixed(1)}`).join(' ')
  const last = ds[ds.length - 1].v
  const lastTxt = m.unit === 'speed' ? Math.round(last * 10) / 10 + ' KB/s' : Math.round(last * 10) / 10 + unitLabel
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">` +
    `<rect x="0" y="0" width="${W}" height="${H}" fill="#fafbfc"/>` +
    `<polyline points="${pts}" fill="none" stroke="${m.color}" stroke-width="1.6" stroke-linejoin="round"/>` +
    `<text x="${W - 8}" y="15" text-anchor="end" font-size="11" fill="#6b7280" font-family="sans-serif">${m.label}（${unitLabel}） 最新 ${lastTxt}</text>` +
    `<text x="8" y="${H - 5}" font-size="10" fill="#9aa1ad" font-family="sans-serif">${fmtTime(ds[0].t)}</text>` +
    `<text x="${W - 8}" y="${H - 5}" text-anchor="end" font-size="10" fill="#9aa1ad" font-family="sans-serif">${fmtTime(ds[ds.length - 1].t)}</text>` +
    `</svg>`
  let b64 = ''
  try {
    b64 = btoa(unescape(encodeURIComponent(svg)))
  } catch {
    return ''
  }
  return `![${m.label} 折线图](data:image/svg+xml;base64,${b64})`
}

export function toMarkdown(
  rangeZh: string,
  genAt: string,
  items: ReportItem[],
  overview: Narrative,
  points: HistPoint[],
): string {
  const n = items.length ? items[0].s.n : 0
  let md = '# nasdeck 历史趋势分析报告\n\n'
  md += `- 数据区间：${rangeZh}（历史数据自动保留 30 天）\n`
  md += `- 生成时间：${genAt}\n`
  md += `- 数据样本：${n} 条（每 30 秒聚合一点）\n\n`
  md += `- 说明：下方折线图为内嵌 SVG 图片。在 VS Code 中请按 **Cmd+Shift+V（Mac）/ Ctrl+Shift+V（Win）** 打开「预览」视图查看真图；代码视图、GitHub 网页、微信中只显示图片链接文字属正常现象（下方「文本趋势线」仍可看趋势）。若需任意环境都为真图，请下载 HTML 版。\n\n`
  md += `## 总体结论\n\n${overview.text}\n\n`
  REPORT_GROUPS.forEach(g => {
    const gs = items.filter(x => g.keys.includes(x.m.key))
    if (!gs.length) return
    md += `## ${g.title}\n\n`
    gs.forEach(x => {
      const pairs = points
        .map(p => ({ ts: p.ts, v: p[x.m.key] }))
        .filter((p): p is { ts: number; v: number } => typeof p.v === 'number' && !isNaN(p.v))
      md += `### ${x.m.label}\n${x.nar ? x.nar.text : ''}\n\n${svgChartImg(x.m, pairs)}\n\n> 趋势（${x.m.label}）：${asciiSpark(x.s.values)}\n\n`
    })
  })
  const warns = items.filter(x => x.nar && x.nar.level !== 'ok')
  if (warns.length) {
    md += `## 异常与建议\n\n`
    warns.forEach(x => (md += `- 【${x.nar && x.nar.level === 'danger' ? '危险' : '关注'}】${x.nar ? x.nar.text : ''}\n`))
    md += `\n建议：优先检查机箱风道与防尘，必要时调整风扇策略或降低负载。\n`
  }
  return md
}

export function toHTML(rangeZh: string, genAt: string, items: ReportItem[], overview: Narrative): string {
  const n = items.length ? items[0].s.n : 0
  const ovClass = overview.level === 'danger' ? 'danger' : overview.level === 'warn' ? 'warn' : 'ok'
  let body = ''
  REPORT_GROUPS.forEach(g => {
    const gs = items.filter(x => g.keys.includes(x.m.key))
    if (!gs.length) return
    body += `<h2>${g.title}</h2>`
    gs.forEach(x => {
      const lvl = x.nar ? x.nar.level : 'ok'
      const badge =
        lvl === 'danger'
          ? '<span class="badge danger">危险</span>'
          : lvl === 'warn'
            ? '<span class="badge warn">关注</span>'
            : '<span class="badge ok">正常</span>'
      body += `<div class="item ${lvl}"><div class="item-h"><span class="dot" style="background:${x.m.color}"></span><b>${x.m.label}</b>${badge}</div><div class="spark">${sparkline(x.s.values, x.m.color)}</div><div class="nar">${x.nar ? x.nar.text : ''}</div></div>`
    })
  })
  const warns = items.filter(x => x.nar && x.nar.level !== 'ok')
  let advice = ''
  if (warns.length) {
    advice = `<div class="block danger-block"><h2>异常与建议</h2><ul>${warns.map(x => `<li><b>${x.m.label}：</b>${x.nar ? x.nar.text : ''}</li>`).join('')}</ul><p>建议：优先检查机箱风道与防尘，必要时调整风扇策略或降低负载。</p></div>`
  }
  const CSS = `*{box-sizing:border-box}body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#f5f6f8;color:#1f2430;margin:0;padding:24px;line-height:1.6}.wrap{max-width:840px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.08)}header h1{font-size:20px;margin:0 0 6px}.meta{color:#6b7280;font-size:13px}.overview{border-radius:10px;padding:14px 16px;margin:18px 0;font-size:15px}.overview.ok{background:#e9f7ef;color:#1b7f4b;border:1px solid #bfe6cf}.overview.warn{background:#fff6e6;color:#9a6a00;border:1px solid #ffe0a3}.overview.danger{background:#fdecec;color:#b42323;border:1px solid #f6c9c9}h2{font-size:16px;margin:22px 0 10px;border-left:3px solid #4aa3ff;padding-left:8px}.item{border:1px solid #eef0f3;border-radius:10px;padding:12px 14px;margin-bottom:10px}.item.danger{border-color:#f3c2c2;background:#fff7f7}.item.warn{border-color:#ffe2b0;background:#fffaef}.item-h{display:flex;align-items:center;gap:8px;font-size:14px}.dot{width:10px;height:10px;border-radius:50%;display:inline-block}.badge{font-size:11px;padding:1px 8px;border-radius:10px;margin-left:auto}.badge.ok{background:#e9f7ef;color:#1b7f4b}.badge.warn{background:#fff6e6;color:#9a6a00}.badge.danger{background:#fdecec;color:#b42323}.spark{margin:8px 0}.nar{font-size:13px;color:#374151}.block.danger-block{background:#fff7f7;border:1px solid #f3c2c2;border-radius:10px;padding:14px 16px;margin-top:18px}.block ul{margin:8px 0;padding-left:20px}.block li{margin:4px 0;font-size:13px}footer{margin-top:24px;color:#9aa1ad;font-size:12px;text-align:center}`
  return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>nasdeck 历史趋势分析报告</title><style>${CSS}</style></head><body><div class="wrap"><header><h1>nasdeck 历史趋势分析报告</h1><div class="meta">数据区间：${rangeZh} · 生成时间：${genAt} · 样本 ${n} 条（每 30 秒一点，自动保留 30 天）</div></header><div class="overview ${ovClass}">${overview.text}</div>${body}${advice}<footer>本报告由 nasdeck 在您的 NAS 本地生成，数据未离开本机。</footer></div></body></html>`
}

export function downloadReport(filename: string, content: string, mime: string, bom: boolean): void {
  const blob = new Blob(bom ? ['\ufeff' + content] : [content], { type: mime + ';charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
