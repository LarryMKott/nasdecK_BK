/**
 * 控制与自动化页业务逻辑：告警配置表单（15s 只刷活动告警不碰表单）、保存/测试通知
 * （写操作，后端 @require_admin）、健康报告导出、运行日志弹窗（10s 自动刷新）、
 * 错误历史圈二级弹窗、toast 与复制。
 * 日志行解析为纯函数在 utils/logParse（可单测）。
 */
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  clearErrors,
  getAlerts,
  getReportHtml,
  getReportJson,
  saveAlertConfig,
  testAlertNotify,
} from '@/api/modules/alert.api'
import type { AlertEntry, ErrorRingEntry } from '@/types/api/alerts.types'
import { ApiError } from '@/api/client'
import { pageCacheGet, pageCacheSet } from '@/composables/common/pageCache'
import { lvlCls, parseLog, statusText as logStatusText } from '@/utils/logParse'
import type { LogEntry } from '@/utils/logParse'

export const LEVELS = [
  { id: 'danger', label: '严重 (danger)' },
  { id: 'warn', label: '警告 (warn)' },
  { id: 'info', label: '信息 (info)' },
] as const

export const CHANNELS = [
  { id: 'system', label: '面板日志' },
  { id: 'telegram', label: 'Telegram' },
  { id: 'bark', label: 'Bark' },
  { id: 'email', label: '邮件' },
] as const

const CACHE_KEY = 'automation'

export function useAutomation() {
  /* ---------------- 配置 / 告警 ---------------- */

  const cfg = reactive({
    enabled: false,
    disk_health: false,
    memory_max: 90 as number | string,
    temp: { enabled: true, cpu_max: 85 as number | string, mb_max: 75 as number | string, disk_max: 60 as number | string },
    channels: {
      system: true,
      telegram: { enabled: false, bot_token: '', chat_id: '' },
      bark: { enabled: false, url: '' },
      email: { enabled: false, smtp_host: '', smtp_port: 465 as number | string, user: '', pass: '', to: '' },
    },
    level_channels: { danger: [] as string[], warn: [] as string[], info: [] as string[] },
  })

  const alerts = ref<AlertEntry[]>([])
  const busy = ref(false)
  const lastUpdate = ref('')
  const saveState = ref('')
  const saveOk = ref(false)
  const exportState = ref('')

  let alertTimer = 0

  function fillConfig(c: Record<string, unknown>): void {
    const src: Record<string, unknown> = c || {}
    cfg.enabled = !!src.enabled
    cfg.disk_health = !!src.disk_health
    cfg.memory_max = src.memory_max != null ? (src.memory_max as number | string) : 90

    const t = (src.temp || {}) as Record<string, unknown>
    cfg.temp.enabled = t.enabled !== false
    cfg.temp.cpu_max = t.cpu_max != null ? (t.cpu_max as number | string) : 85
    cfg.temp.mb_max = t.mb_max != null ? (t.mb_max as number | string) : 75
    cfg.temp.disk_max = t.disk_max != null ? (t.disk_max as number | string) : 60

    const ch = (src.channels || {}) as Record<string, any>
    cfg.channels.system = ch.system !== false
    const tg = ch.telegram || {}
    cfg.channels.telegram.enabled = !!tg.enabled
    cfg.channels.telegram.bot_token = tg.bot_token || ''
    cfg.channels.telegram.chat_id = tg.chat_id || ''
    const bk = ch.bark || {}
    cfg.channels.bark.enabled = !!bk.enabled
    cfg.channels.bark.url = bk.url || ''
    const em = ch.email || {}
    cfg.channels.email.enabled = !!em.enabled
    cfg.channels.email.smtp_host = em.smtp_host || ''
    cfg.channels.email.smtp_port = em.smtp_port != null ? em.smtp_port : 465
    cfg.channels.email.user = em.user || ''
    cfg.channels.email.pass = em.pass || ''
    cfg.channels.email.to = em.to || ''

    const lc = (src.level_channels || {}) as Record<string, unknown>
    for (const lv of LEVELS) {
      cfg.level_channels[lv.id] = Array.isArray(lc[lv.id])
        ? (lc[lv.id] as string[]).filter((x: string) => CHANNELS.some(cc => cc.id === x))
        : []
    }
  }

  function setLastUpdate(at?: string): void {
    if (at) lastUpdate.value = '更新于 ' + at
  }

  async function loadConfig(): Promise<void> {
    // 切页签回来先上缓存秒开（表单值随后会被服务器数据覆盖，未保存的编辑不依赖缓存）
    if (!alerts.value.length) {
      const cached = pageCacheGet<{ config: Record<string, unknown>; alerts: AlertEntry[]; evaluated_at?: string }>(CACHE_KEY)
      if (cached) {
        fillConfig(cached.config || {})
        alerts.value = cached.alerts || []
        setLastUpdate(cached.evaluated_at)
      }
    }
    // 有内容就不转圈：后台静默拉最新
    if (!alerts.value.length) busy.value = true
    try {
      const j = await getAlerts()
      fillConfig(j.config || {})
      alerts.value = j.alerts || []
      setLastUpdate(j.evaluated_at)
      pageCacheSet(CACHE_KEY, { config: j.config || {}, alerts: j.alerts || [], evaluated_at: j.evaluated_at })
    } catch {
      /* 与旧页一致：静默失败，不打断页面 */
    }
    busy.value = false
  }

  /** 15s 只刷「活动告警」，不碰表单 —— 否则会把用户正在编辑的配置冲掉（旧页同口径） */
  async function refreshAlerts(): Promise<void> {
    try {
      const j = await getAlerts()
      alerts.value = j.alerts || []
      setLastUpdate(j.evaluated_at)
    } catch {
      /* 忽略 */
    }
  }

  function lvlHas(lvl: string, ch: string): boolean {
    return (cfg.level_channels as Record<string, string[]>)[lvl].includes(ch)
  }
  function lvlToggle(lvl: string, ch: string, on: boolean): void {
    const arr = (cfg.level_channels as Record<string, string[]>)[lvl]
    const i = arr.indexOf(ch)
    if (on && i < 0) arr.push(ch)
    if (!on && i >= 0) arr.splice(i, 1)
  }
  function onLvlChange(lvl: string, ch: string, ev: Event): void {
    const el = ev.target as HTMLInputElement
    lvlToggle(lvl, ch, !!el.checked)
  }

  /* ---------------- 保存 / 测试通知（写操作） ---------------- */

  function num(v: number | string, d: number): number {
    const n = parseFloat(String(v))
    return Number.isFinite(n) ? n : d
  }
  function int(v: number | string, d: number): number {
    const n = parseInt(String(v), 10)
    return Number.isFinite(n) ? n : d
  }

  function buildPayload(): Record<string, unknown> {
    return {
      enabled: cfg.enabled,
      disk_health: cfg.disk_health,
      memory_max: num(cfg.memory_max, 90),
      temp: {
        enabled: cfg.temp.enabled,
        cpu_max: num(cfg.temp.cpu_max, 85),
        mb_max: num(cfg.temp.mb_max, 75),
        disk_max: num(cfg.temp.disk_max, 60),
      },
      channels: {
        system: cfg.channels.system,
        telegram: {
          enabled: cfg.channels.telegram.enabled,
          bot_token: cfg.channels.telegram.bot_token,
          chat_id: cfg.channels.telegram.chat_id,
        },
        bark: { enabled: cfg.channels.bark.enabled, url: cfg.channels.bark.url },
        email: {
          enabled: cfg.channels.email.enabled,
          smtp_host: cfg.channels.email.smtp_host,
          smtp_port: int(cfg.channels.email.smtp_port, 465),
          user: cfg.channels.email.user,
          pass: cfg.channels.email.pass,
          to: cfg.channels.email.to,
        },
      },
      level_channels: {
        danger: cfg.level_channels.danger.slice(),
        warn: cfg.level_channels.warn.slice(),
        info: cfg.level_channels.info.slice(),
      },
    }
  }

  async function saveConfig(): Promise<void> {
    saveState.value = '保存中…'
    saveOk.value = false
    try {
      const j = await saveAlertConfig(buildPayload())
      if (j.ok) {
        saveState.value = ' 已保存'
        saveOk.value = true
        await refreshAlerts()
      } else {
        saveState.value = '失败：' + (j.error || '未知错误')
        saveOk.value = false
      }
    } catch (e) {
      // 非 2xx（如 403 非管理员）时后端契约体在 ApiError.body 里，沿用同一套「失败：…」文案
      if (e instanceof ApiError) {
        const b = (e.body && typeof e.body === 'object' ? (e.body as { error?: string }) : null) || null
        saveState.value = '失败：' + ((b && b.error) || 'HTTP ' + e.status)
      } else {
        saveState.value = '请求失败'
      }
      saveOk.value = false
    }
  }

  async function testNotify(): Promise<void> {
    saveState.value = '发送中…'
    saveOk.value = false
    try {
      // SMTP / Bark 走外网可能较慢，给足超时
      const j = await testAlertNotify()
      saveState.value = '已尝试（' + JSON.stringify(j.results || {}) + '）'
      saveOk.value = true
    } catch (e) {
      // 旧页对任何可解析响应都显示「已尝试（…）」，仅网络层失败才报「请求失败」——保持同口径
      if (e instanceof ApiError) {
        saveState.value = '已尝试（{}）'
        saveOk.value = true
      } else {
        saveState.value = '请求失败'
        saveOk.value = false
      }
    }
  }

  /* ---------------- 一键健康报告 / 运行日志 ---------------- */

  function downloadBlob(filename: string, content: string, mime: string): void {
    const blob = new Blob([content], { type: mime + ';charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  /** 导出报告时是否脱敏（内网 IP / MAC / 序列号 / 主机名 / 云盘账号） */
  const redactExport = ref(false)

  async function exportReport(fmt: 'json' | 'html'): Promise<void> {
    exportState.value = '导出中…'
    try {
      const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
      if (fmt === 'json') {
        const j = await getReportJson({ redact: redactExport.value })
        downloadBlob('nasdeck-report-' + ts + '.json', JSON.stringify(j, null, 2), 'application/json')
      } else {
        const html = await getReportHtml({ redact: redactExport.value })
        downloadBlob('nasdeck-report-' + ts + '.html', html, 'text/html')
      }
      exportState.value = '已导出'
    } catch (e) {
      exportState.value = '导出失败'
      alert('导出失败：' + (e instanceof Error ? e.message : String(e)))
    }
  }

  const logOpen = ref(false)
  const logLoading = ref(false)
  const logLoadErr = ref('')
  const logEntries = ref<LogEntry[]>([])
  const logRawText = ref('')
  /** 本次拉取日志的时间（弹窗标题右侧显示，方便确认看的是什么时候的日志） */
  const logFetchedAt = ref('')
  /** 错误历史圈（后端内存长期保留，不被 60 行日志尾部刷掉） */
  const errRing = ref<ErrorRingEntry[]>([])

  const nE = computed(() => logEntries.value.filter(e => e.lvl === 'ERROR').length)
  const nW = computed(() => logEntries.value.filter(e => e.lvl === 'WARN').length)
  const errText = computed(() => logEntries.value.filter(e => e.lvl === 'ERROR').map(e => e.raw).join('\n'))
  const warnText = computed(() => logEntries.value.filter(e => e.lvl === 'WARN').map(e => e.raw).join('\n'))

  /** 304 -> OK 绿；3xx/4xx -> 警告黄；5xx -> 错误红（状态码→级别的展示翻译在 utils/logParse） */
  function statusText(e: LogEntry): string {
    return logStatusText(e)
  }

  /** 日志弹窗自动刷新定时器：打开期间每 10 秒静默拉一次最新日志 */
  let logTimer: number | undefined

  async function loadLog(silent = false): Promise<void> {
    if (!silent) {
      logLoading.value = true
      logLoadErr.value = ''
      logEntries.value = []
      logRawText.value = ''
      errRing.value = []
      logFetchedAt.value = ''
    }
    try {
      // 从健康报告 JSON 取 log_tail，避免重复后端实现（旧页同做法）
      const j = await getReportJson()
      const text = j.log_tail || ''
      logRawText.value = text
      logEntries.value = parseLog(text)
      errRing.value = j.error_ring || []
      logFetchedAt.value = new Date().toLocaleTimeString('zh-CN')
    } catch (e) {
      if (!silent) logLoadErr.value = '加载日志失败：' + (e instanceof Error ? e.message : String(e))
      // 静默刷新失败不打扰用户，保留上一次内容
    }
    logLoading.value = false
  }

  async function showRunLog(): Promise<void> {
    logOpen.value = true
    await loadLog()
    stopLogTimer()
    logTimer = window.setInterval(() => {
      if (logOpen.value && !logLoading.value) loadLog(true)
    }, 10000)
  }

  function stopLogTimer(): void {
    if (logTimer) {
      window.clearInterval(logTimer)
      logTimer = undefined
    }
  }

  function closeRunLog(): void {
    logOpen.value = false
    stopLogTimer()
  }

  /** 错误记录二级弹窗 */
  const ringOpen = ref(false)
  /** 复制用的纯文本版（[时间] 内容（连续出现 N 次）） */
  const ringRawText = computed(() =>
    errRing.value.map(e => '[' + e.ts + '] ' + e.text + (e.n > 1 ? '（连续出现 ' + e.n + ' 次）' : '')).join('\n'),
  )
  /** 清除按钮二次确认态：第一下点亮（3 秒内再点才真清） */
  const ringConfirm = ref(false)
  let ringConfirmTimer = 0

  function showRing(): void {
    ringOpen.value = true
  }

  function closeRing(): void {
    ringOpen.value = false
    ringConfirm.value = false
    window.clearTimeout(ringConfirmTimer)
  }

  async function clearErrRing(): Promise<void> {
    // 两步确认：第一下只亮红色警示，3 秒内再点才真清（防手滑，清除不可恢复）
    if (!ringConfirm.value) {
      ringConfirm.value = true
      window.clearTimeout(ringConfirmTimer)
      ringConfirmTimer = window.setTimeout(() => (ringConfirm.value = false), 3000)
      return
    }
    ringConfirm.value = false
    window.clearTimeout(ringConfirmTimer)
    try {
      await clearErrors()
    } catch {
      /* 清除失败不弹错，下次自动刷新会再同步 */
    }
    errRing.value = []
    toastMsg.value = '错误记录已清除'
    window.clearTimeout(toastTimer)
    toastTimer = window.setTimeout(() => (toastMsg.value = ''), 2000)
  }

  function onKey(e: KeyboardEvent): void {
    if (e.key === 'Escape') {
      if (ringOpen.value) closeRing()
      else if (logOpen.value) closeRunLog()
    }
  }

  /* ---------------- toast（复制反馈） ---------------- */

  const toastMsg = ref('')
  let toastTimer = 0
  function toast(msg: string): void {
    toastMsg.value = msg
    window.clearTimeout(toastTimer)
    toastTimer = window.setTimeout(() => { toastMsg.value = '' }, 2600)
  }

  function copyText(txt: string, label: string): void {
    const short = label.replace('复制', '')
    if (!txt) {
      toast('没有可复制的' + short)
      return
    }
    const done = (): void => toast('已复制' + short)
    const fallback = (): void => {
      const ta = document.createElement('textarea')
      ta.value = txt
      document.body.appendChild(ta)
      ta.select()
      try {
        document.execCommand('copy')
        done()
      } catch {
        window.prompt('请手动复制：', txt)
      }
      ta.remove()
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(done).catch(fallback)
    } else {
      fallback()
    }
  }

  /* ---------------- 生命周期 ---------------- */

  onMounted(async () => {
    document.addEventListener('keydown', onKey)
    await loadConfig()
    alertTimer = window.setInterval(refreshAlerts, 15000)
  })

  onUnmounted(() => {
    document.removeEventListener('keydown', onKey)
    if (alertTimer) window.clearInterval(alertTimer)
    window.clearTimeout(toastTimer)
    stopLogTimer()
  })

  return {
    cfg,
    alerts,
    busy,
    lastUpdate,
    loadConfig,
    lvlHas,
    onLvlChange,
    saveState,
    saveOk,
    saveConfig,
    testNotify,
    exportState,
    redactExport,
    exportReport,
    logOpen,
    logLoading,
    logLoadErr,
    logEntries,
    logRawText,
    logFetchedAt,
    errRing,
    nE,
    nW,
    errText,
    warnText,
    lvlCls,
    statusText,
    ringOpen,
    ringRawText,
    ringConfirm,
    showRing,
    closeRing,
    clearErrRing,
    showRunLog,
    closeRunLog,
    copyText,
    toastMsg,
  }
}
