/**
 * API 请求客户端 —— 基址、超时、新鲜度记录、统一错误归一。
 *
 * 铁律（2026-09-10 真机实测）：飞牛网关把第三方应用挂在
 * `/cgi/ThirdParty/<pkg>/index.cgi/` 之下，裸 `/api/*` 只在该根目录下有效；
 * 子路径（如 `/vue/`）会被网关 404。
 * 统一从当前路径截出 `index.cgi` 前缀再拼，任意层级都通。
 *
 * 传输引擎保留 fetch（方案中的 axios 不引入，决策备案于迁移说明）：
 * 1) fetch 原生 credentials + cache:'no-store' + AbortController 超时；
 *    XHR 无 cache 语义，飞牛 WebView 旧内核可能缓存轮询响应；
 * 2) 全部调用点已按 Response 语义（r.ok / r.json()）验证过网关 404→200 的怪癖；
 * 3) 单文件产物不引入额外运行时。分层目标不变：统一基址/超时/错误/新鲜度。
 */
import { reactive } from 'vue'

/**
 * 数据新鲜度：记录各接口（按 path 归一，去掉 query）最后一次成功拿到响应的时间（ms）。
 * 全局页眉据此显示「数据于 N 秒前更新」，超过阈值变黄提示可能滞后。
 */
export const freshness = reactive<{ lastAny: number; lastOk: Record<string, number> }>({
  lastAny: 0,
  lastOk: {},
})

export const API_BASE = (() => {
  const m = location.pathname.match(/^(.*\/index\.cgi)\/?/)
  return m ? m[1] : ''
})()

/**
 * 带超时的 fetch：普通 30s、重接口可传更长，避免飞牛慢通道把请求挂死。
 * init 用于 POST 等（method / headers / body）；credentials / cache / signal 强制由本函数接管，
 * 避免调用方误改导致带不上会话 cookie 或不走 no-store。
 */
export function apiFetch(path: string, ms = 30000, init: RequestInit = {}): Promise<Response> {
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null
  const to = ctrl ? window.setTimeout(() => { try { ctrl.abort() } catch { /* 忽略 */ } }, ms) : 0
  const p = fetch(API_BASE + path, {
    ...init,
    credentials: 'include',
    cache: 'no-store',
    ...(ctrl ? { signal: ctrl.signal } : {}),
  })
  // 记录成功时间（网关把 404 转 200，这里按 r.ok 记；业务失败由调用方另判）
  p.then(r => {
    if (r.ok) {
      const key = path.split('?')[0]
      freshness.lastOk[key] = Date.now()
      freshness.lastAny = Date.now()
    }
  }).catch(() => {})
  return to ? p.finally(() => window.clearTimeout(to)) : p
}

/** 统一 API 错误：非 2xx、网络异常、JSON 解析失败都归一到此处，页面只 catch 一种类型。 */
export class ApiError extends Error {
  /** HTTP 状态码；网络层失败（超时/断网）为 0 */
  readonly status: number
  /** 非 2xx 时尝试解析出的响应体（后端错误契约 {"ok":false,"error":...}），供调用方取细节 */
  readonly body?: unknown
  constructor(message: string, status = 0, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

export interface ApiJsonOptions {
  /** 超时 ms，默认 30s（重接口如 /api/all 传 60000） */
  timeout?: number
  method?: 'GET' | 'POST'
  /** POST 的 JSON body（省略时提交空对象，兼容后端 json_body(silent=True) 契约） */
  body?: unknown
  /** query 参数；值为 undefined 的项跳过（Date.now() 防缓存戳沿用旧调用习惯） */
  query?: Record<string, string | number | undefined>
}

/** 拼 query 串；值为 undefined 的项跳过（Date.now() 防缓存戳沿用旧调用习惯） */
function withQuery(path: string, query?: Record<string, string | number | undefined>): string {
  if (!query) return path
  const qs = Object.entries(query)
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(String(v)))
    .join('&')
  return qs ? path + (path.includes('?') ? '&' : '?') + qs : path
}

/**
 * GET/POST 并把响应解析为 JSON。非 2xx 或非法 JSON 抛 ApiError；
 * 新鲜度记录发生在 apiFetch 内部（凡 2xx 即记，与旧行为逐字一致）。
 */
export async function apiJson<T = unknown>(path: string, opts: ApiJsonOptions = {}): Promise<T> {
  const { timeout = 30000, method = 'GET', body, query } = opts
  const fullPath = withQuery(path, query)
  const init: RequestInit =
    method === 'POST'
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }
      : {}
  const r = await apiFetch(fullPath, timeout, init)
  if (!r.ok) {
    let errBody: unknown
    try {
      errBody = await r.json()
    } catch {
      /* 非 JSON 错误体，忽略 */
    }
    throw new ApiError('HTTP ' + r.status, r.status, errBody)
  }
  try {
    return (await r.json()) as T
  } catch {
    throw new ApiError('响应不是合法 JSON', r.status)
  }
}

/**
 * GET 并返回响应文本（非 JSON 接口用，如 /manual?embed=1 的 HTML 片段）。
 * 非 2xx 抛 ApiError；新鲜度记录与 apiJson 同源。
 */
export async function apiText(
  path: string,
  opts: { timeout?: number; query?: Record<string, string | number | undefined> } = {},
): Promise<string> {
  const { timeout = 30000, query } = opts
  const r = await apiFetch(withQuery(path, query), timeout)
  if (!r.ok) throw new ApiError('HTTP ' + r.status, r.status)
  return r.text()
}
