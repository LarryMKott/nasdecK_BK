"""硬盘 SMART 面板用例：/api/disks 查询与 /api/disks/rename 改名的编排核。

自 presentation/api/endpoints/disks.py 原样搬迁（v2.4 阶段 3 批 A 第三单）：
standalone 实时判定 / 通道标注 / 容错降级逐行等价；
盘名清洗经 domain.rules.disk_rule（纯规则），盘名持久化经 IDiskNamesRepo。
rename 返回 (status, body)，由端点 _jr 透传。
"""
import time

from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.disk_names import IDiskNamesRepo
from backend.domain.interfaces.health_service import IHealthService
from backend.domain.rules.disk_rule import disk_name_clean


def _panel_time() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class DisksUseCase:
    def __init__(self, collector: IHardwareCollector, health: IHealthService, names_repo: IDiskNamesRepo):
        self._c = collector
        self._health = health
        self._names = names_repo

    def panel(self, force: bool = False) -> dict:
        """硬盘 SMART 板块。

        standalone（是否独立盘，决定能否跑破坏性 badblocks）必须实时计算，
        不能跟着 get_disks 的 300s 缓存走——否则用户刚从阵列移除硬盘，
        B 档按钮要等 5 分钟缓存过期才变绿。SMART 等重数据仍走缓存。
        """
        t0 = time.time()
        try:
            cached = self._c.get_disks(force)
            disks = []
            for d in cached:
                d2 = dict(d)
                ok, reason = self._c.is_standalone_disk(d.get("dev"))
                d2["standalone"], d2["standalone_reason"] = ok, reason
                disks.append(d2)
            # 标注通道来源（阵列卡 / 主板），供前端「物理硬盘信息」区分
            try:
                raid = self._c.get_raid_card()
            except Exception:
                raid = {}
            disks = self._health.enrich_disk_channels(disks, raid)
            return {"disks": disks, "time": _panel_time(), "elapsed": round(time.time() - t0, 2)}
        except Exception as e:
            return {"error": str(e), "time": _panel_time()}

    def rename(self, data: dict) -> tuple:
        """设置/清除运维自定义盘名（v2.3.0 3.8）。data: {key, name}；name 空串 = 清除。

        key 用 /api/disks 返回的 name_key（序列号优先，与缺陷趋势同源），
        前端不自己拼 key，避免口径漂移。"""
        key = str(data.get("key") or "").strip()
        if not key or len(key) > 128:
            return 400, {"ok": False, "error": "缺少盘标识"}
        name = disk_name_clean(data.get("name"))
        cfg = self._names.load()
        if name:
            cfg[key] = name
        else:
            cfg.pop(key, None)
        if not self._names.save(cfg):
            return 500, {"ok": False, "error": "保存失败"}
        return 200, {"ok": True, "key": key, "name": name}
