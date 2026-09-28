"""仪表盘用例：/api/all（全量聚合）与 /api/system（系统板块轻量聚合）的编排核。

自 presentation/api/endpoints/all.py 原样搬迁（v2.4 阶段 3）：
编排逻辑逐行等价（并行采集/容错降级/字段拼装/告警复用），差异仅
—— 数据获取全部经构造注入的领域抽象（IHardwareCollector / IHealthService / IAppInfo）。
出参为聚合 dict（键序与既有 API 契约一致；聚合 DTO 以 dict 承载的决策见 dto/__init__.py）。
"""
import time
from concurrent.futures import ThreadPoolExecutor

from backend.domain.interfaces.app_info import IAppInfo
from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.health_service import IHealthService


def _panel_time() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class DashboardUseCase:
    def __init__(self, collector: IHardwareCollector, health: IHealthService, app_info: IAppInfo):
        self._c = collector
        self._health = health
        self._info = app_info

    def execute(self, force: bool = False) -> dict:
        """全量聚合（/api/all 载荷）。"""
        t0 = time.time()
        try:
            def _safe(fn, default):
                try:
                    return fn()
                except Exception:
                    return default
            # 各板块采集相互独立，并行跑，首屏不再被串行累加拖慢
            # （get_system/get_disks 有统一 TTL 缓存，force=1 时手动刷新强制重采）
            with ThreadPoolExecutor(max_workers=8) as ex:
                f_sys = ex.submit(self._c.get_system, force)
                f_board = ex.submit(self._c.get_board)
                f_mem = ex.submit(self._c.get_memory_modules)
                f_raid = ex.submit(self._c.get_raid_card)
                f_disks = ex.submit(self._c.get_disks, force)
                f_storage = ex.submit(self._c.get_storage)
                f_docker = ex.submit(self._c.get_docker)
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
                        system["cpu_usage"] = self._c.get_cpu_usage()
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
                    "nasdash_version": self._info.nasdash_version(),
                    "fnos_version": self._info.fnos_version(),
                }
                # 把阵列卡芯片温度并入 system，供「温度监控」tab 直接读取（随 /api/all /api/system 刷新）
                _raid = result.get("raid")
                if isinstance(_raid, dict):
                    result["system"]["raid_temp"] = _raid.get("roc_temp")
                # 把显卡「实时显存占用」并入 system.gpus，供系统资源页顶部「显存占用」指标格显示。
                # 按 pci 优先、型号次之匹配；匹配不上就不带 mem_* 字段，前端显示「—」。
                try:
                    _live_gpus = self._c.get_gpu_live()
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
                result["disks"] = self._health.enrich_disk_channels(result.get("disks", []) or [], result.get("raid") or {})
                rt = self._c.get_realtime_metrics()
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
                result["alerts"] = self._health.evaluate_alerts(result["system"], result["disks"], result["docker"])
            except Exception:
                result["alerts"] = []
        except Exception as e:
            result = {"error": str(e), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        return result

    def system_panel(self, force: bool = False) -> dict:
        """系统板块轻量聚合（/api/system 载荷）：CPU/内存/温度/GPU/网卡 + 主板/内存条。"""
        t0 = time.time()
        try:
            try:
                board = self._c.get_board()
            except Exception:
                board = {"manufacturer": "", "product": "", "version": ""}
            try:
                memory_modules = self._c.get_memory_modules()
            except Exception:
                memory_modules = {"modules": [], "total_gb": 0, "slots": 0, "brand_summary": ""}
            system = {**self._c.get_system(force), "board": board, "memory_modules": memory_modules}
            try:
                system["cpu_usage"] = self._c.get_cpu_usage()
            except Exception:
                system["cpu_usage"] = None
            # 阵列卡芯片温度（ROC Temperature），供「温度监控」tab 显示（get_raid_card 有 60s 缓存，开销极低）
            try:
                _raid = self._c.get_raid_card()
                if isinstance(_raid, dict):
                    system["raid_temp"] = _raid.get("roc_temp")
            except Exception:
                system["raid_temp"] = None
            # fnos_version 顶层暴露：旧版「关于」页的 fnOS 版本取自 /api/all 的 DATA，
            # 现「关于」Vue 原生页只拉轻量的 /api/system，故在此补一项，避免为一条版本号拉全量 /api/all。
            return {"system": system, "fnos_version": self._info.fnos_version(),
                    "time": _panel_time(), "elapsed": round(time.time() - t0, 2)}
        except Exception as e:
            return {"error": str(e), "time": _panel_time()}
