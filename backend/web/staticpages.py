# -*- coding: utf-8 -*-
"""静态页与操作手册路由（自 app.py L6426-6676 搬迁；Flask → FastAPI 改写）。

【迁移差异】
- send_from_directory → 自实现 _send_under()（FileResponse 无遍历防护，
  这里保留原 Flask 的目录钳制语义：resolve 后必须仍在目标目录内）
- make_response(html, code) → HTMLResponse / FileResponse（状态码与响应头一致）
- Response(stream_with_context(gen())) → StreamingResponse（同步生成器
  由 Starlette 在线程池迭代，chunked 语义不变）
- 资源路径锚点：原 os.path.dirname(__file__)（app.py 在应用根）→ paths.APP_DIR
"""
import os
import re

from fastapi import APIRouter, Request
from fastapi.responses import (FileResponse, HTMLResponse, Response,
                               StreamingResponse)

from backend.paths import APP_DIR
from backend.version import APP_VERSION

router = APIRouter()

# 【配置点】静态资源目录（部署时位于应用根，即 APP_DIR 下）
_UI_IMAGES_DIR = os.path.join(APP_DIR, "ui", "images")
_VUE_DIR = os.path.join(APP_DIR, "templates", "vue")


def _send_under(directory, filename):
    """send_from_directory 直译：目录钳制（防路径穿越）+ FileResponse。"""
    full = os.path.abspath(os.path.join(directory, filename))
    if not full.startswith(os.path.abspath(directory) + os.sep):
        return Response(status_code=404)  # 越界路径（../ 等）
    if not os.path.isfile(full):
        return Response(status_code=404)
    return FileResponse(full)


def _no_cache(resp):
    """原版三件套 no-cache 响应头（/ 与 /manual 共用）。"""
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@router.get("/ui/images/{filename:path}")
def ui_images(filename: str):
    """暴露 ui/images 下的静态图标，供页面内 <img> 引用。"""
    return _send_under(_UI_IMAGES_DIR, filename)


def _vue_missing_page():
    """兜底页：前端构建产物缺失时的可读提示（已无「回退旧页」这条退路）。

    v2.3.0 第 11 步「旧页退休瘦身」起，旧版单页面板 templates/index.html 已删除。
    产物随仓库提交、正常安装不会缺失，这里只保证极端情况下不是一片白屏。
    """
    resp = HTMLResponse(
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>nasdash - 资源缺失</title></head><body style=\"font:14px/1.8 -apple-system,'PingFang SC',sans-serif;"
        "max-width:560px;margin:80px auto;padding:0 24px\">"
        "<h2 style='font-size:18px'>界面资源缺失</h2>"
        "<p style='color:#646a73'>应用的前端文件没找到。请刷新页面重试；若一直如此，请在应用中心重新安装 nasdash。</p>"
        "</body></html>",
        status_code=500,
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


def _serve_vue_app():
    """主界面：frontend-vue/ 构建产物，单文件 HTML。

    产物 `templates/vue/index.html` 已提交进仓库（无 Node 环境也能打包）。
    """
    vue_index = os.path.join(_VUE_DIR, "index.html")
    if not os.path.isfile(vue_index):
        return _vue_missing_page()
    return _no_cache(FileResponse(vue_index, media_type="text/html"))


@router.get("/")
def index():
    """主入口：Vue 应用（11 个模块全部为原生页；第 11 步起已无旧页依赖）。"""
    return _serve_vue_app()


# ===================== /vue/（阶段 1a 旧链接兼容） =====================
# 阶段 2 起 /vue/ 与 / 同物，保留该路径只为让阶段 1a 的旧链接继续可用。
@router.get("/vue")
@router.get("/vue/")
def vue_pilot():
    return _serve_vue_app()


# ===================== 操作手册（/manual 路由，离线可读） =====================
_MANUAL_CSS = """
:root{
  --text:#1f2933; --muted:#64748b; --bg:#f1f5f9; --card:#ffffff;
  --border:#e2e8f0; --blue:#2563eb; --code-bg:#1e293b; --code-fg:#e2e8f0;
}
* { box-sizing:border-box; }
body{ margin:0; background:var(--bg); color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  line-height:1.75; }
.wrap{ max-width:920px; margin:0 auto; padding:32px 20px 80px; }
h1{ font-size:26px; margin:0 0 6px; }
h2{ font-size:21px; margin:34px 0 10px; padding-bottom:6px; border-bottom:2px solid var(--border); }
h3{ font-size:17px; margin:24px 0 8px; color:#0f172a; }
h4{ font-size:15px; margin:18px 0 6px; }
p{ margin:10px 0; }
ul,ol{ margin:10px 0; padding-left:24px; }
li{ margin:5px 0; }
a{ color:var(--blue); }
code{ background:var(--code-bg); color:var(--code-fg); padding:2px 6px; border-radius:5px;
  font-size:13px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
pre{ background:var(--code-bg); color:var(--code-fg); padding:14px; border-radius:8px;
  overflow-x:auto; font-size:13px; }
blockquote{ background:#fff7ed; border-left:4px solid #f59e0b; margin:12px 0;
  padding:10px 16px; border-radius:6px; color:#92400e; }
hr{ border:none; border-top:1px solid var(--border); margin:28px 0; }
.man-table{ border-collapse:collapse; width:100%; margin:12px 0; font-size:14px;
  display:table; overflow:visible; }
.man-table th,.man-table td{ border:1px solid var(--border); padding:8px 12px; text-align:left; }
.man-table th{ background:var(--card); font-weight:600; }
.man-table tbody tr:nth-child(even){ background:#f8fafc; }
.topbar{ position:sticky; top:0; background:var(--card); border-bottom:1px solid var(--border);
  padding:10px 20px; font-size:13px; color:var(--muted); z-index:10; }
.topbar b{ color:var(--text); }
.hl-red{ color:#dc2626; font-weight:500; }
"""
# 应用内嵌版：去掉整页外壳/顶栏/定宽，宽度自适应面板；前端 query embed=1 时返回
_MANUAL_CSS_EMBED = """
.man-body{ color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; line-height:1.75; max-width:100%; }
.man-body h1{ font-size:24px; margin:0 0 8px; color:var(--text); }
.man-body h2{ font-size:19px; margin:28px 0 10px; padding-bottom:6px; border-bottom:2px solid var(--border); color:var(--text); }
.man-body h3{ font-size:16px; margin:20px 0 8px; color:var(--text); }
.man-body h4{ font-size:14px; margin:16px 0 6px; color:var(--text); }
.man-body p{ margin:10px 0; color:var(--text); }
.man-body ul,.man-body ol{ margin:10px 0; padding-left:24px; color:var(--text); }
.man-body li{ margin:5px 0; }
.man-body a{ color:var(--primary); }
.man-body code{ background:var(--fill); color:var(--text); padding:2px 6px; border-radius:5px; font-size:13px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.man-body pre{ background:var(--fill); color:var(--text); padding:14px; border-radius:8px; overflow-x:auto; font-size:13px; }
.man-body blockquote{ background:var(--bg); border-left:4px solid var(--warning); margin:12px 0; padding:10px 16px; border-radius:6px; color:var(--text); }
.man-body hr{ border:none; border-top:1px solid var(--border); margin:28px 0; }
.man-body .man-table{ border-collapse:collapse; width:100%; margin:12px 0; font-size:14px; display:table; overflow:visible; }
.man-body .man-table th,.man-body .man-table td{ border:1px solid var(--border); padding:8px 12px; text-align:left; }
.man-body .man-table th{ background:var(--card); font-weight:600; }
.man-body .man-table tbody tr:nth-child(even){ background:var(--fill); }
.man-body .hl-red{ color:var(--danger); font-weight:500; }
"""


def _md_inline(text):
    """行内：转义 HTML + **粗体** + `代码` + ==红色== + <mark>红色</mark>。
    红色标记给「本版本新增/修复」高亮用：App 内显示红色，GitHub 原生黄底高亮。"""
    # 先抽离 <mark>...</mark>，避免被下面统一转义吃掉标签（内容内部的 **粗体** 仍会处理）
    _marks = []
    def _stash_mark(m):
        _marks.append(m.group(1))
        return '\x00M%d\x00' % (len(_marks) - 1)
    text = re.sub(r'<mark>(.+?)</mark>', _stash_mark, text, flags=re.S)
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    # 先吃 ==红色== 标记（避免被后面的 ** 误吃），用占位符包起来再做粗体/代码
    text = re.sub(r'==(.+?)==', lambda m: '\x00HL\x00' + m.group(1) + '\x00/HL\x00', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = text.replace('\x00HL\x00', '<span class="hl-red">').replace('\x00/HL\x00', '</span>')
    # 还原 <mark> 为红色 span（内部 **粗体** 已处理）
    for i, _m in enumerate(_marks):
        text = text.replace('\x00M%d\x00' % i, '<span class="hl-red">%s</span>' % _m)
    text = re.sub(r'`([^`]+?)`', r'<code>\1</code>', text)
    return text


def _render_markdown_stream(md):
    """流式版：逐行/逐块 yield HTML 片段，配合 /manual 的 chunked 响应边传边显。"""
    lines = md.split('\n')
    in_list = False; list_type = None
    table_rows = []; in_table = False

    def close_list():
        nonlocal in_list, list_type
        if in_list:
            yield '</%s>' % list_type; in_list = False; list_type = None
    def close_table():
        nonlocal in_table, table_rows
        if in_table:
            yield '</tbody></table>'; in_table = False; table_rows = []

    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        # 表格
        if s.startswith('|') and s.count('|') >= 2:
            if not in_table:
                in_table = True; table_rows = []
            cells = [c.strip() for c in s.strip('|').split('|')]
            if cells and all(set(c) <= set('-: ') and c for c in cells):
                i += 1; continue  # 分隔行
            table_rows.append(cells)
            nxt = lines[i+1].strip() if i+1 < len(lines) else ''
            if not (nxt.startswith('|') and nxt.count('|') >= 2):
                for chunk in close_list():
                    yield chunk
                yield '<table class="man-table"><thead><tr>' \
                           + ''.join('<th>%s</th>' % _md_inline(c) for c in table_rows[0]) \
                           + '</tr></thead><tbody>'
                for r in table_rows[1:]:
                    yield '<tr>' + ''.join('<td>%s</td>' % _md_inline(c) for c in r) + '</tr>'
                yield '</tbody></table>'
                in_table = False; table_rows = []
            i += 1; continue
        for chunk in close_table():
            yield chunk
        if s == '---':
            for chunk in close_list():
                yield chunk
            yield '<hr>'; i += 1; continue
        if s.startswith('#'):
            for chunk in close_list():
                yield chunk
            lvl = len(s) - len(s.lstrip('#'))
            yield '<h%d>%s</h%d>' % (lvl, _md_inline(s.lstrip('#').strip()), lvl); i += 1; continue
        if s.startswith('>'):
            for chunk in close_list():
                yield chunk
            yield '<blockquote>%s</blockquote>' % _md_inline(s.lstrip('>').strip()); i += 1; continue
        st = line.lstrip()
        if st.startswith('- ') or st.startswith('* '):
            if not in_list or list_type != 'ul':
                for chunk in close_list():
                    yield chunk
                yield '<ul>'; in_list = True; list_type = 'ul'
            yield '<li>%s</li>' % _md_inline(st[2:].strip()); i += 1; continue
        m = re.match(r'^\d+\.\s+(.*)$', st)
        if m:
            if not in_list or list_type != 'ol':
                for chunk in close_list():
                    yield chunk
                yield '<ol>'; in_list = True; list_type = 'ol'
            yield '<li>%s</li>' % _md_inline(m.group(1)); i += 1; continue
        if not s:
            for chunk in close_list():
                yield chunk
            i += 1; continue
        for chunk in close_list():
            yield chunk
        yield '<p>%s</p>' % _md_inline(s); i += 1
    for chunk in close_list():
        yield chunk
    for chunk in close_table():
        yield chunk


@router.get("/manual")
def manual(request: Request):
    p = os.path.join(APP_DIR, "docs", "使用手册.md")
    try:
        md = open(p, "r", encoding="utf-8").read()
    except Exception as e:
        resp = HTMLResponse("<h1>操作手册未找到</h1><p>%s</p>" % _md_inline(str(e)), status_code=404)
        return _no_cache(resp)
    if request.query_params.get("embed") == "1":
        # 应用内嵌：仅返回片段（无 html/head/body 外壳），由前端塞进面板，停留在应用内
        # 同样走流式，前端用 reader 边收边 append，避免大手册整段等待
        head = "<style>%s</style>\n<div class=\"man-body\">" % _MANUAL_CSS_EMBED
        tail = "</div>"
    else:
        head = ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<title>nasdash 操作手册</title><style>%s</style></head>"
                "<body><div class=\"topbar\">nasdash 操作手册 · <b>v%s</b> · "
                "<a href=\"/\">← 返回面板</a></div>"
                "<div class=\"wrap\">") % (_MANUAL_CSS, APP_VERSION)
        tail = "</div></body></html>"
    # no-store：手册走网关反代，不缓存否则首次打开可能拿到空白/旧响应（同 / 路由）
    # 流式（chunked）：后端边渲染边推给网关/浏览器，手册长也不卡在「加载中」
    def gen():
        yield head
        for chunk in _render_markdown_stream(md):
            yield chunk
        yield tail
    resp = StreamingResponse(gen(), media_type="text/html")
    resp.headers["X-Accel-Buffering"] = "no"  # 关掉反代缓冲，确保 chunked 真生效
    return _no_cache(resp)
