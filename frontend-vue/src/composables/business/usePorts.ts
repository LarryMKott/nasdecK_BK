/**
 * 端口占用页业务逻辑：10s 轮询 + 去重/搜索/来源筛选 + 释放端口（写操作）。
 * 「来源」下拉框的键盘/外点关闭等交互态留在视图层；本模块提供筛选数据与方法。
 */
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { getPorts, portAppIconUrl, releasePort } from '@/api/modules/network.api'
import type { PortInfo, PortReleaseResp } from '@/types/api/ports.types'
import type { HeroStat } from '@/types/common.types'
import { ApiError } from '@/api/client'
import { pageCacheGet, pageCacheSet } from '@/composables/common/pageCache'

const CACHE_KEY = 'ports'

/** 按来源归类：应用 / 容器 / 系统（与表格徽章三类一一对应）。 */
export type PortKind = 'app' | 'docker' | 'host'
export type ScopeFilter = 'all' | PortKind

export interface ScopeInfo {
  cls: string
  type: string
  name: string
  /** 原始标识（appid / 容器名），鼠标悬停时显示，便于排障时对号入座。 */
  raw?: string
}

export interface ReachInfo {
  cls: string
  text: string
  hint: string
}

/** 可达范围三级徽章（后端按「绑了哪张网卡」判定，不写死 IP 段）。 */
const REACH: Record<string, ReachInfo> = {
  lan: { cls: 'b-warn', text: '对外', hint: '局域网里任何机器都能连（绑所有网卡 / 物理网卡 IP / 广播 / 组播）' },
  docker: { cls: 'b-info', text: '容器网', hint: '只有本机与 Docker 容器网络能连，局域网连不上' },
  lo: { cls: 'b-muted', text: '仅本机', hint: '仅 127.0.0.1 / ::1，出了这台 NAS 谁都连不上' },
}

/** 状态值中文化。只查监听态，所以取值只有两个：
 *  TCP 恒 LISTEN、UDP 恒 UNCONN（UDP 没有「建立连接」的概念，「未连接」＝端口已绑好可收包）。 */
const STATE_TEXT: Record<string, string> = { LISTEN: '监听中', UNCONN: '未连接' }
export const STATE_HINT: Record<string, string> = {
  LISTEN: '端口已开好，等待别人来连接',
  UNCONN: 'UDP 不建立连接：端口已绑好、可以收包（含义等同于 TCP 的「监听中」）',
}

export function usePorts() {
  const ports = ref<PortInfo[]>([])
  const lastUpdate = ref('')
  const busy = ref(false)
  const error = ref('')
  const releasing = ref(false)
  const msg = ref('')
  const msgType = ref<'ok' | 'err'>('ok')

  // 释放二次确认弹窗
  const confirmOpen = ref(false)
  const target = ref<PortInfo | null>(null)

  let timer = 0
  let fetching = false

  const uniquePorts = computed<PortInfo[]>(() => {
    // ss 对通配地址（0.0.0.0 / [::] / *）可能重复输出同一端口，按 proto+port+pid 去重，
    // 优先保留具体地址、丢弃 "*"。
    const seen = new Map<string, PortInfo>()
    for (const p of ports.value) {
      const key = p.proto + '|' + p.port + '|' + (p.pid ?? -1)
      const ex = seen.get(key)
      if (!ex) {
        seen.set(key, p)
      } else if (ex.local === '*' && p.local !== '*') {
        seen.set(key, p)
      }
    }
    return Array.from(seen.values())
  })

  /**
   * 同一端口被多个进程占用的情况（UDP 允许端口复用；Docker 也会为 IPv4/IPv6 各起一个
   * docker-proxy，是**两个真实进程**）——这些行**不合并**，只统计数量供表格分组展示。
   */
  const groupSize = computed<Map<string, number>>(() => {
    const m = new Map<string, Set<number>>()
    for (const p of uniquePorts.value) {
      const k = p.proto + '|' + p.port
      if (!m.has(k)) m.set(k, new Set())
      m.get(k)!.add(p.pid ?? -1)
    }
    const out = new Map<string, number>()
    m.forEach((v, k) => out.set(k, v.size))
    return out
  })

  const keyword = ref('')
  const kw = computed(() => keyword.value.trim().toLowerCase())

  function kindOf(p: PortInfo): PortKind {
    const s = p.scope || ''
    if (s.startsWith('app:')) return 'app'
    if (s.startsWith('docker')) return 'docker'
    return 'host'
  }

  const scopeFilter = ref<ScopeFilter>('all')
  /** 来源筛选项（下拉用）。 */
  const FILTERS: { id: ScopeFilter; label: string }[] = [
    { id: 'all', label: '全部' },
    { id: 'app', label: '应用' },
    { id: 'docker', label: '容器' },
    { id: 'host', label: '系统' },
  ]
  const scopeCounts = computed<Record<ScopeFilter, number>>(() => {
    const c: Record<ScopeFilter, number> = { all: uniquePorts.value.length, app: 0, docker: 0, host: 0 }
    for (const p of uniquePorts.value) c[kindOf(p)]++
    return c
  })
  /** 标题旁常显的分布（应用 / 容器 / 系统各多少个），补回下拉藏起来的信息。 */
  const DIST: { id: PortKind; label: string; cls: string }[] = [
    { id: 'app', label: '应用', cls: 'd-app' },
    { id: 'docker', label: '容器', cls: 'd-docker' },
    { id: 'host', label: '系统', cls: 'd-host' },
  ]

  function pickFilter(id: ScopeFilter): void {
    scopeFilter.value = id
  }

  /** 搜索过滤：端口号 / 地址 / 协议 / 进程名 / PID / 应用名 / 来源说明，任一命中即可。
   *  与来源筛选（应用/容器/系统）叠加生效。 */
  const filteredPorts = computed<PortInfo[]>(() => {
    const q = kw.value
    const f = scopeFilter.value
    return uniquePorts.value.filter(p => {
      if (f !== 'all' && kindOf(p) !== f) return false
      if (!q) return true
      const si = scopeInfo(p)
      const hay = [
        p.proto, String(p.port), p.local, p.process, p.pid != null ? String(p.pid) : '',
        p.app, p.appname, p.iconname, p.desc, si.type, si.name,
        reachInfo(p)?.text || '', stateText(p), isKernel(p) ? '内核 内核态服务' : '',
      ].join(' ').toLowerCase()
      return hay.includes(q)
    })
  })

  /** 是否在用搜索框（只搜索时才显示「匹配 X / Y 条」）。
   *  纯类别筛选不显示——类别有多少条，下拉按钮和左侧分布行已经各写了一遍。 */
  const searching = computed(() => kw.value !== '')

  /** 该行是否「同一端口的后续占用者」（紧跟上一行且 proto+port 相同）——用于缩进展示。 */
  function isSub(p: PortInfo, i: number): boolean {
    if (i <= 0) return false
    const prev = filteredPorts.value[i - 1]
    return prev.proto === p.proto && prev.port === p.port
  }
  /** 该端口的占用者数量（>1 时由续行标出「↳ 同一端口（n/N）」）。 */
  function portGroup(p: PortInfo): number {
    return groupSize.value.get(p.proto + '|' + p.port) || 1
  }
  /** 该行在同端口占用组内的序号（从 1 起）——续行据此显示「（2/3）」，顺序即表格里从上到下。 */
  function groupOrder(p: PortInfo, i: number): number {
    let n = 1
    for (let k = i - 1; k >= 0; k--) {
      const q = filteredPorts.value[k]
      if (q.proto === p.proto && q.port === p.port) n++
      else break
    }
    return n
  }

  const tcpCount = computed(() => uniquePorts.value.filter(p => p.proto === 'tcp').length)
  const udpCount = computed(() => uniquePorts.value.filter(p => p.proto === 'udp').length)
  const publicCount = computed(() => uniquePorts.value.filter(p => p.public).length)

  const heroStats = computed<HeroStat[]>(() => [
    { v: String(uniquePorts.value.length), k: '监听端口' },
    { v: tcpCount.value + '/' + udpCount.value, k: 'TCP/UDP' },
    { v: String(publicCount.value), k: '对外暴露' },
  ])

  async function load(force = false): Promise<void> {
    if (fetching) return
    fetching = true
    if (force) busy.value = true
    if (!ports.value.length) {
      const cached = pageCacheGet<PortsRespShape>(CACHE_KEY)
      if (cached && cached.ports) ports.value = cached.ports
    }
    try {
      const j = await getPorts()
      ports.value = j.ports || []
      pageCacheSet(CACHE_KEY, { ports: ports.value })
      error.value = j.error || ''
      lastUpdate.value = '更新于 ' + new Date().toLocaleTimeString('zh-CN')
      msg.value = ''
    } catch (e) {
      if (e instanceof ApiError) error.value = '端口接口返回异常（HTTP ' + e.status + '）'
      else error.value = '获取端口数据失败：' + (e instanceof Error ? e.message : String(e))
    } finally {
      fetching = false
      busy.value = false
    }
  }

  function scopeInfo(p: PortInfo): ScopeInfo {
    const s = p.scope || ''
    if (s.startsWith('app:')) {
      return { cls: 'b-ok', type: '应用', name: p.appname || p.app || s.slice(4), raw: p.app || s.slice(4) }
    }
    if (s.startsWith('docker')) {
      const cname = s.startsWith('docker:') ? s.slice(7) : ''
      // 容器名若本身就是个应用中心应用（商店装的 docker 应用），优先显示应用名：
      // cf-dns-select → CF DNS 优选；纯 docker 容器没有应用名，仍显示容器名。
      return { cls: 'b-info', type: '容器', name: p.iconname || cname || 'Docker', raw: cname }
    }
    // host：系统服务
    return { cls: 'b-warn', type: '系统', name: p.desc || '飞牛系统服务' }
  }

  function reachInfo(p: PortInfo): ReachInfo | null {
    return REACH[p.reach] || null
  }

  function stateText(p: PortInfo): string {
    return STATE_TEXT[p.state] || p.state
  }

  /** 内核态 socket：内核线程（如 NFS 的 nfsd / lockd，ps 里显示成 [nfsd]）持有的端口，
   *  `ss -p` 读不到 users 段，所以没有进程名、也没有 PID。后端已按端口号反查补了说明。 */
  function isKernel(p: PortInfo): boolean {
    return p.kernel === true || (!p.process && p.pid == null)
  }

  /** 应用图标：后端按 appid 从应用安装目录取它自带的图标（找不到 404 → 前端回退纯文字）。 */
  const iconFailed = reactive<Record<string, boolean>>({})
  function appIconUrl(app: string): string {
    return portAppIconUrl(app)
  }
  function onIconError(app: string): void {
    if (app) iconFailed[app] = true
  }

  function askRelease(p: PortInfo): void {
    if (p.pid == null) {
      msg.value = '该端口无关联进程 PID，无法释放（可能是内核 / 系统套接字）'
      msgType.value = 'err'
      return
    }
    target.value = p
    confirmOpen.value = true
  }

  function showReleaseResult(j: PortReleaseResp, pid: number, proc: string): void {
    if (j.ok) {
      msg.value = '已向进程 ' + pid + '（' + (j.process || proc) + '）发送终止信号，端口将释放'
      msgType.value = 'ok'
    } else {
      msg.value = (j.blocked ? '已拦截：' : '释放失败：') + (j.error || '未知错误')
      msgType.value = 'err'
    }
  }

  async function doRelease(): Promise<void> {
    if (!target.value || target.value.pid == null) return
    confirmOpen.value = false
    releasing.value = true
    msg.value = ''
    const pid = target.value.pid
    const proc = target.value.process
    target.value = null
    try {
      const j = await releasePort(pid)
      showReleaseResult(j, pid, proc)
    } catch (e) {
      // 非 2xx（如 403 非管理员）时后端契约体在 ApiError.body 里，按同一套文案展示
      if (e instanceof ApiError && e.body && typeof e.body === 'object') {
        showReleaseResult(e.body as PortReleaseResp, pid, proc)
      } else {
        msg.value = '释放请求失败：' + (e instanceof Error ? e.message : String(e))
        msgType.value = 'err'
      }
    } finally {
      releasing.value = false
      // 稍等进程退出、端口释放后再刷新
      window.setTimeout(() => void load(true), 1200)
    }
  }

  function closeConfirm(): void {
    confirmOpen.value = false
    target.value = null
  }

  function refreshAll(): void {
    void load(true)
  }

  onMounted(() => {
    void load(true)
    timer = window.setInterval(() => void load(false), 10000)
  })
  onUnmounted(() => {
    if (timer) window.clearInterval(timer)
  })

  return {
    ports,
    uniquePorts,
    keyword,
    kw,
    scopeFilter,
    FILTERS,
    DIST,
    scopeCounts,
    pickFilter,
    filteredPorts,
    searching,
    isSub,
    portGroup,
    groupOrder,
    heroStats,
    lastUpdate,
    busy,
    error,
    releasing,
    msg,
    msgType,
    confirmOpen,
    target,
    kindOf,
    scopeInfo,
    reachInfo,
    stateText,
    isKernel,
    iconFailed,
    appIconUrl,
    onIconError,
    askRelease,
    doRelease,
    closeConfirm,
    refreshAll,
  }
}

/** 页面缓存形状（pageCache 存 { ports }） */
interface PortsRespShape {
  ports?: PortInfo[]
}
