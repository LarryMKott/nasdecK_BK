from backend.presentation.api.endpoints.helpers import _panel_time

from backend.infrastructure.collectors.board import get_board
from backend.infrastructure.collectors.board import get_memory_modules
from backend.infrastructure.collectors.cpuusage import get_cpu_usage
from backend.infrastructure.collectors.disks import get_disks
from backend.infrastructure.collectors.dockermod import get_docker
from backend.infrastructure.collectors.gpu import _get_gpu_live
from backend.infrastructure.collectors.metrics import get_realtime_metrics
from backend.infrastructure.collectors.raid import get_raid_card
from backend.infrastructure.collectors.storage import get_storage
from backend.infrastructure.collectors.system import get_system
from backend.infrastructure.health.alerts import _evaluate_alerts
from backend.infrastructure.health.diskhealth import _enrich_disk_channels
from backend.infrastructure.version import APP_VERSION
from backend.infrastructure.version import _fnos_version
import time

# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
from fastapi import APIRouter, Depends, Request

from backend.infrastructure.version import _fnos_version

from backend.infrastructure.gateway import require_admin
from backend.presentation.api.dependencies import _jr, json_body

router = APIRouter()



@router.get("/api/all")
def api_all(request: Request):
    t0 = time.time()
    force = request.query_params.get("force") == "1"
    from concurrent.futures import ThreadPoolExecutor
    try:
        def _safe(fn, default):
            try:
                return fn()
            except Exception:
                return default
        # 各板块采集相互独立，并行跑，首屏不再被串行累加拖慢
        # （get_system/get_disks 有统一 TTL 缓存，force=1 时手动刷新强制重采）
        with ThreadPoolExecutor(max_workers=8) as ex:
            f_sys = ex.submit(get_system, force)
            f_board = ex.submit(get_board)
            f_mem = ex.submit(get_memory_modules)
            f_raid = ex.submit(get_raid_card)
            f_disks = ex.submit(get_disks, force)
            f_storage = ex.submit(get_storage)
            f_docker = ex.submit(get_docker)
            try:
                board = f_board.result()
            except Exception:
                board = {"manufacturer": "", "product": "", "version": ""}
            try:
                memory_modules = f_mem.result()
            except Exception:
                memory_modules = {"modules": [], "total_gb": 0, "slots": 0, "brand_summary": ""}
            try:
                system = f_sys.result()
                # 首屏直接拿真实 CPU 使用率；_warmup_caches 已预热基准，不再短 sleep。
                try:
                    system["cpu_usage"] = get_cpu_usage()
                except Exception:
                    system["cpu_usage"] = None
            except Exception:
                system = {}
            result = {
                "raid": _safe(f_raid.result, []),
                "disks": _safe(f_disks.result, []),
                "system": {**system, "board": board, "memory_modules": memory_modules},
                "storage": _safe(f_storage.result, {}),
                "docker": _safe(f_docker.result, {}),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "elapsed": round(time.time() - t0, 2),
                "nasdash_version": APP_VERSION,
                "fnos_version": _fnos_version(),
            }
            # 把阵列卡芯片温度并入 system，供「温度监控」tab 直接读取（随 /api/all /api/system 刷新）
            _raid = result.get("raid")
            if isinstance(_raid, dict):
                result["system"]["raid_temp"] = _raid.get("roc_temp")
            # 把显卡「实时显存占用」并入 system.gpus，供系统资源页顶部「显存占用」指标格显示。
            # 静态 gpus 只有型号与显存容量，实时占用来自 _get_gpu_live()（独显驱动直读，核显走 i915 GEM）。
            # 按 pci 优先、型号次之匹配；匹配不上就不带 mem_* 字段，前端显示「—」。
            try:
                _live_gpus = _get_gpu_live()
                for _sg in (result["system"].get("gpus") or []):
                    for _lg in _live_gpus:
                        _pci_ok = _sg.get("pci") and _lg.get("pci") and _sg["pci"] == _lg["pci"]
                        _nm_ok = (_sg.get("name") or "") and _sg.get("name") == _lg.get("name")
                        if _pci_ok or _nm_ok:
                            _sg["mem_used"] = _lg.get("mem_used")
                            _sg["mem_total"] = _lg.get("mem_total")
                            _sg["mem_pct"] = _lg.get("mem_pct")
                            _sg["mem_basis"] = _lg.get("mem_basis")
                            break
            except Exception:
                pass
        try:
            # 给每块盘标注通道来源（阵列卡 / 主板），供前端「物理硬盘信息」区分
            result["disks"] = _enrich_disk_channels(result.get("disks", []) or [], result.get("raid") or {})
            rt = get_realtime_metrics()
            result["net"] = rt["net"]
            result["diskio"] = rt["disk"]
            # 给 diskio 补上型号/容量/品牌等友好标识，方便用户识别 sda/sdb 对应哪块盘
            disk_map = {d["dev"]: d for d in result.get("disks", [])}
            for d in result["diskio"]:
                info = disk_map.get(d["device"], {})
                d["model"] = info.get("model", "")
                d["size"] = info.get("size", "")
                d["brand"] = info.get("brand", "")
                d["type"] = info.get("type", "")
                d["serial"] = info.get("serial", "")
        except Exception:
            pass
        # 活动告警（复用已采集数据，无额外命令开销）
        try:
            result["alerts"] = _evaluate_alerts(result["system"], result["disks"], result["docker"])
        except Exception:
            result["alerts"] = []
    except Exception as e:
        result = {"error": str(e), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    return _jr(result)

# 【补遗】api_system 在初版生成脚本中漏映射，从 git 基线（e9dcec4）手工迁移。
# 板块独立接口：切换导航只拉当前板块，避免全量 /api/all。
@router.get("/api/system")
def api_system(request: Request):
    """系统资源板块（CPU/内存/温度/风扇/GPU/网卡 + 主板/内存条），供 #system 与 #fan 按需刷新。"""
    t0 = time.time()
    try:
        try:
            board = get_board()
        except Exception:
            board = {"manufacturer": "", "product": "", "version": ""}
        try:
            memory_modules = get_memory_modules()
        except Exception:
            memory_modules = {"modules": [], "total_gb": 0, "slots": 0, "brand_summary": ""}
        system = {**get_system(request.query_params.get("force") == "1"), "board": board, "memory_modules": memory_modules}
        try:
            system["cpu_usage"] = get_cpu_usage()
        except Exception:
            system["cpu_usage"] = None
        # 阵列卡芯片温度（ROC Temperature），供「温度监控」tab 显示（get_raid_card 有 60s 缓存，开销极低）
        try:
            _raid = get_raid_card()
            if isinstance(_raid, dict):
                system["raid_temp"] = _raid.get("roc_temp")
        except Exception:
            system["raid_temp"] = None
        # fnos_version 顶层暴露：旧版「关于」页的 fnOS 版本取自 /api/all 的 DATA，
        # 现「关于」Vue 原生页只拉轻量的 /api/system，故在此补一项，避免为一条版本号拉全量 /api/all。
        return _jr({"system": system, "fnos_version": _fnos_version(), "time": _panel_time(), "elapsed": round(time.time() - t0, 2)})
    except Exception as e:
        return _jr({"error": str(e), "time": _panel_time()})
