# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
from fastapi import APIRouter, Depends, Request

from backend.gateway import require_admin
from backend.web.deps import _jr, json_body

router = APIRouter()



@router.get("/api/disks")
def api_disks(request: Request):
    """硬盘 SMART 板块。

    standalone（是否独立盘，决定能否跑破坏性 badblocks）必须实时计算，
    不能跟着 get_disks 的 300s 缓存走——否则用户刚从阵列移除硬盘，
    B 档按钮要等 5 分钟缓存过期才变绿。SMART 等重数据仍走缓存。
    """
    t0 = time.time()
    try:
        cached = get_disks(request.query_params.get("force") == "1")
        disks = []
        for d in cached:
            d2 = dict(d)
            ok, reason = _is_standalone_disk(d.get("dev"))
            d2["standalone"], d2["standalone_reason"] = ok, reason
            disks.append(d2)
        # 标注通道来源（阵列卡 / 主板），供前端「物理硬盘信息」区分
        try:
            raid = get_raid_card()
        except Exception:
            raid = {}
        disks = _enrich_disk_channels(disks, raid)
        return _jr({"disks": disks, "time": _panel_time(), "elapsed": round(time.time() - t0, 2)})
    except Exception as e:
        return _jr({"error": str(e), "time": _panel_time()})

@router.post("/api/disks/rename")
def api_disks_rename(request: Request, payload: dict = Depends(json_body)):
    """设置/清除运维自定义盘名（v2.3.0 3.8）。body: {key, name}；name 空串 = 清除。

    key 用 /api/disks 返回的 name_key（序列号优先，与缺陷趋势同源），
    前端不自己拼 key，避免口径漂移。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "请求格式错误"}, 400)
    key = str(data.get("key") or "").strip()
    if not key or len(key) > 128:
        return _jr({"ok": False, "error": "缺少盘标识"}, 400)
    name = _disk_name_clean(data.get("name"))
    cfg = _disk_names_load()
    if name:
        cfg[key] = name
    else:
        cfg.pop(key, None)
    if not _save_json_file(_disk_names_path(), cfg):
        return _jr({"ok": False, "error": "保存失败"}, 500)
    return _jr({"ok": True, "key": key, "name": name})

@router.get("/api/disks/selftest")
def api_disks_selftest_get():
    """返回当前硬盘自检任务状态（只读）。"""
    with DISK_TEST_LOCK:
        jobs = {}
        for k, v in DISK_TEST_JOBS.items():
            jobs[k] = {kk: vv for kk, vv in v.items() if kk not in ("pid", "_proc", "outfile")}
    return _jr({"ok": True, "jobs": jobs})

@router.get("/api/disks/selftest/history")
def api_disks_selftest_history(request: Request):
    """返回硬盘自检历史记录（新→旧，最多 50 条）。"""
    hist = _load_disk_test_history()
    hist = list(reversed(hist))
    dev = (request.query_params.get("dev") or "").strip()
    if dev:
        hist = [r for r in hist if r.get("dev") == dev]
    return _jr({"ok": True, "history": hist})

@router.post("/api/disks/selftest")
def api_disks_selftest_post(request: Request, payload: dict = Depends(json_body), _admin = Depends(require_admin)):
    """启动硬盘自检：type=long（SMART 长自检）/ badblocks（破坏性，仅独立盘）/ surface（只读表面扫描，所有盘）。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    dev = data.get("dev", "")
    ttype = data.get("type", "")
    confirm = data.get("confirm", False)
    if not isinstance(dev, str) or not _safe_token(dev):
        return _jr({"ok": False, "error": "非法设备名"}, 400)
    if ttype not in ("long", "badblocks", "surface"):
        return _jr({"ok": False, "error": "type 须为 long / badblocks / surface"}, 400)
    disks = get_disks()
    if not any(d.get("dev") == dev for d in disks):
        return _jr({"ok": False, "error": "未找到该硬盘"}, 400)
    with DISK_TEST_LOCK:
        if dev in DISK_TEST_JOBS and DISK_TEST_JOBS[dev]["state"] == "running":
            return _jr({"ok": False, "error": "该硬盘已有正在进行的自检"}, 400)
    if ttype == "badblocks":
        if not confirm:
            return _jr({"ok": False, "error": "B 类坏块慢扫为破坏性测试，请确认"}, 400)
        ok, reason = _is_standalone_disk(dev)
        if not ok:
            return _jr({"ok": False, "error": reason}, 400)
    if ttype == "surface":
        if not confirm:
            return _jr({"ok": False, "error": "C 类表面扫描耗时较长，请确认"}, 400)
    try:
        if ttype == "long":
            _start_smart_long(dev)
        elif ttype == "surface":
            _start_badblocks(dev, mode="surface")
        else:
            _start_badblocks(dev, mode="destructive")
        return _jr({"ok": True})
    except Exception as e:
        return _jr({"ok": False, "error": str(e)}), 500

@router.post("/api/disks/selftest/abort")
def api_disks_selftest_abort(request: Request, payload: dict = Depends(json_body), _admin = Depends(require_admin)):
    """中止指定硬盘的自检任务。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    dev = data.get("dev", "")
    if not isinstance(dev, str) or not _safe_token(dev):
        return _jr({"ok": False, "error": "非法设备名"}, 400)
    with DISK_TEST_LOCK:
        job = DISK_TEST_JOBS.get(dev)
    if not job:
        return _jr({"ok": False, "error": "无进行中的自检"}, 400)
    if job["state"] != "running":
        return _jr({"ok": False, "error": "该硬盘自检已结束"}, 400)
    try:
        if job["type"] == "long":
            _abort_smart_long(dev)
        else:
            _abort_badblocks(dev)
        return _jr({"ok": True})
    except Exception as e:
        return _jr({"ok": False, "error": str(e)}), 500