import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiJson, apiText, ApiError, freshness } from '@/api/client'

function okResp(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status })
}

/** 带显式签名的 fetch mock（否则 vi.fn 推断出零参签名，mock.calls 取值过不了类型检查） */
function fetchOf(resp: () => Response) {
  return vi.fn((_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => Promise.resolve(resp()))
}

beforeEach(() => {
  freshness.lastAny = 0
  for (const k of Object.keys(freshness.lastOk)) delete freshness.lastOk[k]
  vi.unstubAllGlobals()
})

describe('apiJson', () => {
  it('GET 成功：解析 JSON 并记录新鲜度（path 去掉 query）', async () => {
    const spy = fetchOf(() => okResp({ ok: 1 }))
    vi.stubGlobal('fetch', spy)
    const data = await apiJson<{ ok: number }>('/api/version', { query: { force: 1 } })
    expect(data).toEqual({ ok: 1 })
    // jsdom 下 location.pathname='/'，API_BASE 为空串，直接拼 path
    expect(spy.mock.calls[0][0]).toBe('/api/version?force=1')
    expect(freshness.lastOk['/api/version']).toBeGreaterThan(0)
    expect(freshness.lastAny).toBeGreaterThan(0)
  })

  it('query 中 undefined 项跳过、已有 ? 的 path 用 & 追加', async () => {
    const spy = fetchOf(() => okResp({}))
    vi.stubGlobal('fetch', spy)
    await apiJson('/api/all?force=1', { query: { _: 123, skip: undefined } })
    expect(spy.mock.calls[0][0]).toBe('/api/all?force=1&_=123')
  })

  it('POST：JSON 头 + 序列化 body（缺省提交空对象）', async () => {
    const spy = fetchOf(() => okResp({ ok: true }))
    vi.stubGlobal('fetch', spy)
    await apiJson('/api/ports/release', { method: 'POST', body: { pid: 7 } })
    const init = spy.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json')
    expect(init.body).toBe('{"pid":7}')
    await apiJson('/api/errors/clear', { method: 'POST' })
    expect(spy.mock.calls[1]![1]!.body).toBe('{}')
  })

  it('非 2xx 抛 ApiError（status 与后端错误体都带上）', async () => {
    vi.stubGlobal('fetch', fetchOf(() => okResp({ ok: false, error: '需要管理员' }, 403)))
    const err = await apiJson('/api/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(403)
    expect((err as ApiError).body).toEqual({ ok: false, error: '需要管理员' })
  })

  it('2xx 但 body 非法 JSON 抛 ApiError', async () => {
    vi.stubGlobal('fetch', fetchOf(() => new Response('<html>not json</html>', { status: 200 })))
    const err = await apiJson('/api/y').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).message).toContain('JSON')
  })

  it('请求实现强制 credentials + no-store（飞牛网关会话与禁缓存放不下手）', async () => {
    const spy = fetchOf(() => okResp({}))
    vi.stubGlobal('fetch', spy)
    await apiJson('/api/system')
    const init = spy.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('include')
    expect(init.cache).toBe('no-store')
  })
})

describe('apiText', () => {
  it('返回文本（/manual?embed=1 HTML 片段场景）', async () => {
    vi.stubGlobal('fetch', fetchOf(() => new Response('<div>手册</div>', { status: 200 })))
    const html = await apiText('/manual', { query: { embed: 1 } })
    expect(html).toBe('<div>手册</div>')
  })
  it('非 2xx 抛 ApiError', async () => {
    vi.stubGlobal('fetch', fetchOf(() => new Response('gone', { status: 500 })))
    const err = await apiText('/manual').catch((e: unknown) => e)
    expect((err as ApiError).status).toBe(500)
  })
})
