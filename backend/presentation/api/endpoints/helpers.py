# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
# v2.4 阶段 3：get_live_mem / _disks_meta_map（含 300s TTL 缓存）已平移至 infrastructure
# 采集器（collectors/system.py、collectors/disks.py），经 IHardwareCollector 供用例调用；
# 本文件仅剩板块时间戳小工具（raid.py 在用）。
import time


# ===================== 按板块独立接口（切换导航只拉当前板块，避免全量 /api/all）=====================
def _panel_time():
    return time.strftime("%Y-%m-%d %H:%M:%S")
