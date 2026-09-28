"""历史趋势查询用例：/api/history 的编排核（降采样点渲染）。

自 presentation/api/endpoints/metrics.py 原样搬迁（v2.4 阶段 3 批 A 第三单，计划 history_query_usecase）：
range→秒换算 / 桶宽计算 / 行→点渲染逐行等价（fan_rpm_avg 取整、其余 1 位小数、NULL 保留）；
SQL 聚合经 IHistoryRepo.metric_points（查询核下沉基础设施，_db_lock 互斥语义不变）。
"""
import time

from backend.domain.interfaces.history_repo import IHistoryRepo

_RANGE_SECS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


class HistoryQueryUseCase:
    def __init__(self, history: IHistoryRepo):
        self._h = history

    def points(self, rng: str = "24h") -> dict:
        """返回历史趋势降采样点（磁盘读/写速率），按 range 时间桶聚合，省流量。"""
        try:
            secs = _RANGE_SECS.get(rng, 86400)
            end = int(time.time()); start = end - secs
            bucket = max(60, secs // 240)
            rows = self._h.metric_points(start, bucket)

            def _v(row, idx, digits=1):
                v = row[idx]
                if v is None:
                    return None
                return round(v, digits)
            points = [{"ts": r[0], "disk_read": _v(r, 1), "disk_write": _v(r, 2),
                       "net_rx": _v(r, 3), "net_tx": _v(r, 4),
                       "cpu_power": _v(r, 5),
                       "cpu_temp": _v(r, 6), "mb_temp": _v(r, 7),
                       "gpu_temp": _v(r, 8), "disk_temp_max": _v(r, 9),
                       "raid_temp": _v(r, 10), "fan_rpm_avg": _v(r, 11, 0),
                       "mem_used_pct": _v(r, 12)} for r in rows]
            return {"range": rng, "points": points, "bucket_s": bucket}
        except Exception as e:
            return {"error": str(e), "points": []}
