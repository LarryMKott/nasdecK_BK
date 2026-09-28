/** 运行日志解析 —— 纯函数集（无状态），自 Automation 视图抽出，可单测。 */

export type LogLvl = 'ERROR' | 'WARN' | 'INFO'

export interface LogEntry {
  ts: string
  lvl: LogLvl
  txt: string
  raw: string
  /** 访问日志专用列：请求（方法 + 路径，已去掉缓存参数与协议尾巴） */
  req?: string
  /** HTTP 状态码（如 200） */
  status?: string
  /** 响应大小（人性化格式，如 2.8 KB） */
  size?: string
}

/** 12/Sep/2026 00:53:01 这类格式统一成 09-12 00:53:01（年份极少排障用不到，省宽度） */
const MONTH_NUM: Record<string, string> = {
  Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06',
  Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12',
}

function normTime(dd: string, mon: string, hhmmss: string): string {
  const m = MONTH_NUM[mon] || mon
  return `${m}-${dd} ${hhmmss}`
}

function fmtBytes(n: number): string {
  if (!isFinite(n)) return '—'
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1024 / 1024).toFixed(2) + ' MB'
}

/** 304 -> OK 绿；3xx/4xx -> 警告黄；5xx -> 错误红 */
function statusLvl(code: number): LogLvl {
  if (code >= 500) return 'ERROR'
  if (code >= 300) return 'WARN'
  return 'INFO'
}

/** 状态码翻译成普通用户能懂的话（悬停可看原始状态码，给开发者排查用） */
export function statusText(e: LogEntry): string {
  if (!e.status) return e.lvl === 'ERROR' ? '错误' : e.lvl === 'WARN' ? '警告' : '—'
  const code = parseInt(e.status, 10)
  if (code >= 500) return '服务器出错'
  if (code >= 400) return '请求异常'
  if (code >= 300) return '重定向'
  return '成功'
}

/** 与 panel.css 的 .log-line.lv-err / .lv-warn 对齐（旧页误写成 lv-error，见迁移前视图头注释） */
export function lvlCls(lvl: LogLvl): string {
  return lvl === 'ERROR' ? 'err' : lvl === 'WARN' ? 'warn' : 'info'
}

/** 解析混合日志（nginx 风格访问行 + 应用日志行），错误/警告置顶排序。 */
export function parseLog(text: string): LogEntry[] {
  const out: LogEntry[] = []
  for (const line of String(text || '').split('\n')) {
    const s = line.replace(/^\s+|\s+$/g, '')
    if (!s) continue

    // 访问日志（nginx 风格）：IP - - [12/Sep/2026 00:53:01] "GET /api/x HTTP/1.1" 200 2907
    // IP 是访问来源（面板经本机网关转发，恒为本机），对排障无意义，不展示；
    // 路径里的 ?_=时间戳 是前端防缓存的随机参数，也一并去掉
    const am = s.match(/^\S+ \S+ \S+ \[(\d{2})\/(\w{3})\/\d{4} (\d{2}:\d{2}:\d{2})\] "([A-Z]+) (\S+)(?: [^"]*)?" (\d{3}) (\d+|-)$/)
    if (am) {
      const code = parseInt(am[6], 10)
      const path = am[5].replace(/[?&]_=\d+/g, '').replace(/\?$/, '')
      out.push({
        ts: normTime(am[1], am[2], am[3]),
        lvl: statusLvl(code),
        txt: s,
        raw: s,
        req: am[4] + ' ' + path,
        status: String(code),
        size: am[7] === '-' ? '—' : fmtBytes(parseInt(am[7], 10)),
      })
      continue
    }

    // 应用日志：2026-09-12 00:53:01 消息内容
    const m = s.match(/^\[?\d{4}-(\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\]?[ ,]*(.*)$/)
    const ts = m ? `${m[1]} ${m[2]}` : ''
    const txt = m ? m[3] : s
    const low = txt.toLowerCase()
    let lvl: LogLvl = 'INFO'
    if (
      low.indexOf('traceback') >= 0 || low.indexOf('error') >= 0 || low.indexOf('exception') >= 0 ||
      low.indexOf('failed') >= 0 || txt.indexOf('失败') >= 0 || txt.indexOf('错误') >= 0
    ) {
      lvl = 'ERROR'
    } else if (low.indexOf('warn') >= 0 || low.indexOf('warning') >= 0 || txt.indexOf('警告') >= 0) {
      lvl = 'WARN'
    }
    out.push({ ts, lvl, txt, raw: s })
  }
  const order: Record<LogLvl, number> = { ERROR: 0, WARN: 1, INFO: 2 }
  return out.sort((a, b) => order[a.lvl] - order[b.lvl])
}
