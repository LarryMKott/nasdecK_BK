# -*- coding: utf-8 -*-
"""错误历史圈（自 app.py L14-79 搬迁；不再在 import 期安装）。

背景：运行日志弹窗只展示 app.log 最后 60 行，而 app.log 里混着每条 HTTP 请求
记录（风扇 1s 轮询一分钟就能灌满 60 行），一个报错很快就被冲出窗口、找不回来。
方案：包一层 stderr——报错类行（Traceback/Error/Exception/failed/错误/失败/异常）
在照常写日志的同时，额外留存一份在内存（最后 100 条、连续重复合并计数、带捕获时间），
随健康报告 error_ring 字段暴露给前端置顶展示。

【迁移差异】`sys.stderr = _StderrTee(sys.stderr)` 原为 import 期副作用，
现改为 install_error_ring() 由 serve.main() 启动序列显式调用。
【迁移差异】访问行识别正则双格式：werkzeug（原）+ uvicorn（新服务器），
两者都维持「仅 5xx 访问行入圈」规则，避免 /api/errors/* 等路径误收。
"""
import collections
import re
import sys
import time

_ERR_RING = collections.deque(maxlen=100)

# werkzeug 访问行：`127.0.0.1 - - [date] "GET / HTTP/1.1" 200 -`
_ACCESS_WERKZEUG = re.compile(r'^\S+ \S+ \S+ \[[^\]]+\] "[^"]*" (\d{3}) ')
# uvicorn 访问行：`INFO:     127.0.0.1:port - "GET / HTTP/1.1" 200 OK`
_ACCESS_UVICORN = re.compile(r'^(?:INFO|WARNING):\s+\S+ - "[^"]*" (\d{3}) ')


class _StderrTee:
    """透传 stderr 并逐行扫描，报错类行留存进 _ERR_RING。"""

    def __init__(self, raw):
        self.raw = raw
        self._buf = ""

    def write(self, s):
        try:
            self.raw.write(s)
        except Exception:
            pass
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._scan(line)
        return len(s)

    def _scan(self, line):
        s = line.rstrip()
        if not s:
            return
        # HTTP 访问行默认不进圈：路径里碰巧带 error 字样的正常请求（包括
        # /api/errors/clear 自己）会被误收，造成「越清越多」。
        # 访问行只有 5xx（真出事）才留；其余行走关键词判断。
        is_5xx = False
        m_acc = _ACCESS_WERKZEUG.match(s) or _ACCESS_UVICORN.match(s)
        if m_acc:
            if int(m_acc.group(1)) < 500:
                return
            is_5xx = True
        else:
            low = s.lower()
            if not ("traceback" in low or "error" in low or "exception" in low or "failed" in low
                    or "错误" in s or "失败" in s or "异常" in s):
                return
        # 行若自带「[YYYY-MM-DD HH:MM:SS]」时间戳前缀，剥掉并用它作捕获时间
        # （避免错误记录弹窗里捕获时间列与内容开头重复显示同一时间）
        m_ts = re.match(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] ?(.*)$', s)
        if m_ts:
            ring_ts, body = m_ts.group(1), m_ts.group(2)
        else:
            ring_ts, body = time.strftime("%Y-%m-%d %H:%M:%S"), s
        if _ERR_RING:
            last = _ERR_RING[-1]
            if last["text"] == body:
                # 与上一条相同：合并计数，避免循环报错灌圈
                last["n"] += 1
                return
        _ERR_RING.append({"ts": ring_ts, "text": body[:400], "n": 1})

    def flush(self):
        try:
            self.raw.flush()
        except Exception:
            pass


def install_error_ring():
    """安装 stderr 错误圈（幂等）。由 serve.main() 启动序列调用。"""
    if not isinstance(sys.stderr, _StderrTee):
        sys.stderr = _StderrTee(sys.stderr)
