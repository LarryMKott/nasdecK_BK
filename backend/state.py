# -*- coding: utf-8 -*-
"""跨模块共享状态单点归属（重构计划 2.2 节）。

原则：一个状态对象只在一个模块定义；其他模块一律 import 引用（可变对象原地
改写）或经归属模块 API 改写（重赋值型状态）。本模块只收真正跨功能区读写的
6 项；单一功能区内部的状态（_metrics_prev/_CUR、_DISKS_CACHE 等）随归属
模块私有化，不进这里。
"""
import threading

# ---- 真实 CPU 使用率缓存（自 app.py L486-487 原样搬迁）----
# v=最新使用率, t=写入时间, prev=上一次 /proc/stat(total,idle)
# 写：collectors.cpuusage._cpu_sample_1s / daemon.metrics 主循环；读：get_cpu_usage
_CPU_USAGE_CACHE = {"v": None, "t": 0.0, "prev": None}
_CPU_USAGE_LOCK = threading.Lock()

# ---- 全局风扇目标状态（自 app.py L531-532 原样搬迁）----
# key=(hwmon, idx) -> {"mode":"manual"|"auto", "target":0-255}
# 写：web 路由 api_fan_set 与 fans.control 平滑循环共享，必须持 FAN_LOCK 互斥
FAN_LOCK = threading.Lock()
FAN_TARGETS = {}

# ---- 统一温度快照（自 app.py L992-993 原样搬迁）----
# 唯一写者：collectors.cputemp._temp_collect_loop（后台线程周期刷新）
# 读者：风扇控速 / 存储卷 / GPU / 历史采样 / 前端温度接口
_TEMP_SNAP = {"t": 0.0, "cpu_temp": None, "mb_temp": None, "temps": [], "voltages": [], "disks": {}, "raid_drives": [], "raid_temp": None, "raid_controller_temp": None}
_TEMP_SNAP_LOCK = threading.Lock()
