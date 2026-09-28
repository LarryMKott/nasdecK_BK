import { describe, expect, it } from 'vitest'
import { lvlCls, parseLog, statusText } from '@/utils/logParse'

describe('parseLog', () => {
  it('解析 nginx 风格访问行（去缓存参数、算状态级与人读大小）', () => {
    const rows = parseLog(
      '127.0.0.1 - - [12/Sep/2026 00:53:01] "GET /api/history?range=24h&_=1726102381000 HTTP/1.1" 200 2907',
    )
    expect(rows).toHaveLength(1)
    const r = rows[0]
    expect(r.lvl).toBe('INFO')
    expect(r.req).toBe('GET /api/history?range=24h')
    expect(r.status).toBe('200')
    expect(r.size).toBe('2.8 KB')
    expect(r.ts).toBe('09-12 00:53:01')
  })

  it('5xx 访问行判为 ERROR、3xx/4xx 判为 WARN（statusLvl 语义）', () => {
    const rows = parseLog(
      [
        '127.0.0.1 - - [12/Sep/2026 00:53:01] "GET /api/x HTTP/1.1" 500 10',
        '127.0.0.1 - - [12/Sep/2026 00:53:02] "GET /api/y HTTP/1.1" 404 10',
        '127.0.0.1 - - [12/Sep/2026 00:53:03] "GET /api/z HTTP/1.1" 302 -',
      ].join('\n'),
    )
    expect(rows.map(r => r.lvl)).toEqual(['ERROR', 'WARN', 'WARN'])
    expect(rows[2].size).toBe('—') // 302 行响应大小为 "-" → 人读为 —
  })

  it('应用日志行按关键词分级并置顶排序（错误 > 警告 > 信息）', () => {
    const rows = parseLog(
      [
        '2026-09-12 00:53:01 常规信息一条',
        '2026-09-12 00:53:02 something failed badly',
        '2026-09-12 00:53:03 warning: 磁盘温度偏高警告',
      ].join('\n'),
    )
    expect(rows.map(r => r.lvl)).toEqual(['ERROR', 'WARN', 'INFO'])
    expect(rows[2].txt).toBe('常规信息一条')
  })

  it('空行跳过、空输入返回空数组', () => {
    expect(parseLog('')).toEqual([])
    expect(parseLog('\n\n  \n')).toEqual([])
  })
})

describe('statusText / lvlCls', () => {
  it('状态码翻译', () => {
    expect(statusText({ ts: '', lvl: 'INFO', txt: '', raw: '', status: '200' })).toBe('成功')
    expect(statusText({ ts: '', lvl: 'WARN', txt: '', raw: '', status: '404' })).toBe('请求异常')
    expect(statusText({ ts: '', lvl: 'ERROR', txt: '', raw: '', status: '502' })).toBe('服务器出错')
    expect(statusText({ ts: '', lvl: 'ERROR', txt: '', raw: '' })).toBe('错误')
  })
  it('分级类名与 CSS 对齐（lv-err/lv-warn/lv-info，非 lv-error）', () => {
    expect(lvlCls('ERROR')).toBe('err')
    expect(lvlCls('WARN')).toBe('warn')
    expect(lvlCls('INFO')).toBe('info')
  })
})
