"""风扇控制面板用例：/api/fan/* 十二端点的编排核。

自 presentation/api/endpoints/fans.py 原样搬迁（v2.4 阶段 3 批 B）：
校验链 / 别名归并 / key 漂移校正 / 出参形状逐行等价。
硬件与状态访问经 IFanDriver / IFanLabelsRepo / IFanRulesRepo /
IHardwareCollector / IHistoryRepo 五个领域接口；接管开关活读/活写
infrastructure 单点全局（modes._FAN_CTRL_ENABLED，经 driver 中转），
修复拆包以来端点 NameError、控速循环快照读、启动未恢复三处断链。
写路径返回 (status, body)，由端点 _jr 透传。
"""
import json
import os
import threading
import time

from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.fan_driver import IFanDriver
from backend.domain.interfaces.fan_labels import IFanLabelsRepo
from backend.domain.interfaces.fan_rules import IFanRulesRepo
from backend.domain.interfaces.history_repo import IHistoryRepo
from backend.domain.rules.fan_rule import NVME_START_TEMP

_RANGE_SECS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


class FanControlUseCase:
    def __init__(self, driver: IFanDriver, collector: IHardwareCollector,
                 labels: IFanLabelsRepo, rules: IFanRulesRepo, history: IHistoryRepo):
        self._d = driver
        self._c = collector
        self._labels = labels
        self._rules = rules
        self._history = history

    def set(self, data: dict) -> tuple:
        """设置风扇转速：设目标 PWM（后台缓变线程平滑过渡）或恢复自动控温"""
        if not self._d.control_enabled():
            return 400, {"ok": False, "error": "风扇接管已关闭，nasdeck 不控速（仅监控）"}
        hwmon = data.get("hwmon")
        idx = data.get("idx")
        mode = data.get("mode")
        pwm = data.get("pwm")
        # 安全：仅允许本机 hwmon 路径，防止路径注入
        if not isinstance(hwmon, str) or not hwmon.startswith("/sys/class/hwmon/hwmon"):
            return 400, {"ok": False, "error": "invalid hwmon"}
        try:
            idx = int(idx)
            if idx < 1 or idx > 10:
                raise ValueError()
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "invalid idx"}
        # 防御：hwmon 路径可能跨重启/进程漂移(如 nct6797 在 hwmon3↔hwmon4 间变化)，
        # 而 FAN_TARGETS 与平滑线程都以「实时枚举」为权威 key。若 GUI 发来的 (hwmon,idx)
        # 不在实时枚举中、但该 idx 存在，则按 idx 校正 hwmon，确保目标命中真正的可控通道。
        _enum = self._d.enumerate_fans()
        if (hwmon, idx) not in _enum:
            _idx2hw = {i: h for (h, i) in _enum}
            if idx in _idx2hw:
                hwmon = _idx2hw[idx]
        # 别名组：同一物理风扇多通道（用户为这些通道设了相同名称）→ 控速同步到全部成员
        _keys = self._labels.alias_members(hwmon, idx)
        if mode == "auto":
            ext = self._d.ext_service_running()
            for (h, i) in _keys:
                if ext:
                    # 系统风扇服务在跑：交还它接管（写 enable=2）
                    try:
                        self._d.set_enable(h, i, 2)
                    except Exception:
                        pass
                    self._d.pop_target((h, i))
                else:
                    # 系统风扇服务未运行：nasdeck 自带保守温控曲线接管
                    self._d.set_target((h, i), {"mode": "auto", "target": None})
                self._d.save_fan_mode(i, "auto", None)
            owner = "ext_service" if ext else "nasdeck"
            return 200, {"ok": True, "mode": "auto", "owner": owner}
        try:
            pct = int(pwm)
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "invalid pwm"}
        FLOOR = 0  # 允许命令到 0%（真正停转）。有用户反馈其硬件在 10% 已停转，需要能设到 0%
        pct = max(FLOOR, min(100, pct))
        raw = round(pct / 100 * 255)
        # 仅设为目标值，由缓变线程平滑过渡（不再瞬间写 255，避免突然全速）
        for (h, i) in _keys:
            self._d.set_target((h, i), {"mode": "manual", "target": raw})
            self._d.save_fan_mode(i, "manual", raw)
        return 200, {"ok": True, "mode": "manual", "pwm": pct, "raw": raw, "aliased": len(_keys) > 1}

    def pwm_mode(self, data: dict) -> tuple:
        """切换风扇接口的调速信号类型：PWM(4pin 脉宽) ↔ DC(3pin 直流电压)。"""
        hwmon = data.get("hwmon")
        idx = data.get("idx")
        mode = (data.get("pwm_mode") or "").strip().lower()
        if not isinstance(hwmon, str) or not hwmon.startswith("/sys/class/hwmon/hwmon"):
            return 400, {"ok": False, "error": "invalid hwmon"}
        try:
            idx = int(idx)
            if idx < 1 or idx > 10:
                raise ValueError()
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "invalid idx"}
        if mode not in ("pwm", "dc"):
            return 400, {"ok": False, "error": "invalid pwm_mode"}
        # 与 /api/fan/set 一致：hwmon 编号可能跨重启漂移，按实时枚举校正
        _enum = self._d.enumerate_fans()
        if (hwmon, idx) not in _enum:
            _idx2hw = {i: h for (h, i) in _enum}
            if idx in _idx2hw:
                hwmon = _idx2hw[idx]
        cur = self._d.read_pwm_mode(hwmon, idx)
        if cur is None:
            return 400, {"ok": False, "error": "该接口不支持切换调速方式（主板未提供此寄存器）"}
        if cur == mode:
            return 200, {"ok": True, "pwm_mode": mode, "changed": False}
        ok, err = self._d.set_pwm_mode(hwmon, idx, mode)
        if not ok:
            return 400, {"ok": False, "error": err, "pwm_mode": cur}
        return 200, {"ok": True, "pwm_mode": mode, "changed": True}

    def status(self) -> dict:
        """轻量风扇状态：供前端高频轮询，实时显示转速/当前占空比/目标（常驻线程 2s tick）"""
        # fcs 只读 15s TTL 缓存（_fcs_status_cached 绝不在此跑 systemctl），供前端在「接管关闭」时
        # 说清现在由谁在管风扇（v2.3.0 3.1）。
        _fs = self._d.fcs_status()
        return {
            "fans": self._d.get_status(),
            "control_enabled": self._d.control_enabled(),
            "fan_drivers": self._labels.read_drivers(),
            "fcs": {
                "running": bool(_fs.get("running")),
                "installed": bool(_fs.get("installed") or _fs.get("enabled")),
                "configured": self._d.fcs_board_cfg(),
                "disabled_by_user": bool(_fs.get("disabled_by_user")),
            },
        }

    def control_get(self) -> dict:
        """风扇接管总开关状态。"""
        return {"enabled": self._d.control_enabled()}

    def control_set(self, data: dict) -> tuple:
        """开关风扇接管：关闭后 nasdeck 不再根据温度自动调速。若飞牛自带风扇服务已配置，
        会交还它接管；否则保持当前转速、不再写 PWM。"""
        enabled = bool(data.get("enabled", True))
        if not self._d.takeover(enabled):
            return 500, {"ok": False, "error": "写入配置失败"}
        return 200, {"ok": True, "enabled": enabled}

    def temps(self) -> dict:
        """温度统一快照：CPU/主板/各硬盘/全量测点/阵列卡，供前端「当前温度」卡与温度墙高频展示。
        全部数据来自后台采集循环的 _TEMP_SNAP（~2s 刷新），不再各自采样。"""
        _snap = self._c.get_temp_snapshot()
        cpu_T = _snap.get("cpu_temp")
        mb_T = _snap.get("mb_temp")
        states = _snap.get("disks") or {}
        devs = list(states.keys())
        disks = []
        for d in devs:
            st = states.get(d, {})
            fi = self._c.disk_friendly_info(d)
            disks.append({
                "dev": d,
                "name": fi["name"],
                "model": fi["model"],
                "category": fi["category"],
                "is_system": fi["is_system"],
                "temp": st.get("temp"),
                "asleep": st.get("asleep"),
                "no_sleep": st.get("no_sleep", False),
                # 供前端标注「被动散热、不参与风扇温控」，并说明其判定阈值与机械盘不同
                "is_nvme": st.get("is_nvme", False),
                "nvme_start_temp": NVME_START_TEMP,
                # 接口类型（SAS/空）：供温度页给 SAS 盘加身份小徽标
                "intf": (st.get("intf") or "").upper(),
            })
        # 同型号同容量的盘（典型：双磁臂 SAS 盘拆成两个 LUN、或买了两块一样的盘）友好名会撞车，
        # 撞车时补上内核名后缀，保证「一眼能认出是哪块」这个目标不被重名破坏。
        _name_count = {}
        for it in disks:
            _name_count[it["name"]] = _name_count.get(it["name"], 0) + 1
        for it in disks:
            if _name_count.get(it["name"], 0) > 1:
                it["name"] = "%s·%s" % (it["name"], str(it["dev"]).replace("/dev/", ""))
        return {
            "cpu_temp": round(cpu_T, 1) if isinstance(cpu_T, (int, float)) else None,
            "mb_temp": round(mb_T, 1) if isinstance(mb_T, (int, float)) else None,
            "disks": disks,
            # 阵列卡物理盘温度（RAID5 等虚拟盘场景下物理盘不暴露 /dev，OS 盘列表看不到，
            # 只能从 storcli 拿；已按序列号与上方 OS 可见盘去重）：
            "raid_drives": _snap.get("raid_drives") or [],
            # 温度墙测点全量 + 阵列卡芯片温度：前端温度页 5s 实时刷新用（不再等 30s 快照）
            "sensors": _snap.get("temps") or [],
            "raid_temp": _snap.get("raid_temp"),
            "raid_controller_temp": _snap.get("raid_controller_temp"),
            # 显卡温度：复用 GPU 实时采样缓存（2s 刷新），仅取温度相关字段，供温度墙展示。
            # iGPU（Intel 核显）temp 取 CPU 封装温度（同 die）；dGPU 走各自驱动。
            # 附带显存占用（供系统资源页顶部 hero「显存占用」指标格）：
            # 独显走驱动真实显存；核显取 i915 图形缓冲实际占用（共享内存口径，见 mem_basis）。
            "gpus": [
                {"name": gg.get("name"), "vendor": gg.get("vendor"),
                 "type": gg.get("type"), "temp": gg.get("temp"),
                 "mem_used": gg.get("mem_used"), "mem_total": gg.get("mem_total"),
                 "mem_pct": gg.get("mem_pct"), "mem_basis": gg.get("mem_basis")}
                for gg in self._c.get_gpu_live()
            ],
        }

    def disk_temp_get(self) -> dict:
        """读取硬盘温度控风扇配置 + 实时监控盘温度/休眠 + 计算所得目标PWM"""
        cfg = self._rules.load_disk_temp()
        devs = cfg.get("disks", [])
        # 未设监控盘白名单时，与控速循环保持一致：自动用本机全部盘作温度源，
        # 否则 preview 永远空、与实际控制态（已用全部盘）不一致。
        if not devs:
            devs = self._c.list_all_disk_devs()
        states = self._c.get_disk_temps_cached(devs) if devs else {}
        disks_out = [{
            "dev": dev,
            "temp": states.get(dev, {}).get("temp"),
            "asleep": states.get(dev, {}).get("asleep"),
            "no_sleep": states.get(dev, {}).get("no_sleep", False),
        } for dev in devs]
        target = self._rules.disk_temp_pwm(states, cfg)
        return {
            "config": cfg,
            "disks": disks_out,
            "computed_pwm": round(target / 255 * 100) if target is not None else None,
            "computed_raw": target,
        }

    def disk_temp_set(self, data: dict) -> tuple:
        cfg = self._rules.load_disk_temp()
        if "disks" in data:
            if not isinstance(data["disks"], list):
                return 400, {"ok": False, "error": "disks 需为数组"}
            norm = []
            for d in data["disks"]:
                # 前端勾选值可能是短名（sda / nvme0n1），也可能是已带 /dev/ 的全路径
                devname = str(d["dev"] if isinstance(d, dict) and "dev" in d else d)
                dd = devname if devname.startswith("/dev/") else "/dev/" + devname
                if not os.path.exists(dd):
                    return 400, {"ok": False, "error": "设备不存在: " + dd}
                norm.append(dd)
            cfg["disks"] = norm
        if "sleep_stop" in data:
            cfg["sleep_stop"] = bool(data["sleep_stop"])
        if "idle_minutes" in data:
            try:
                iv = float(data["idle_minutes"])
            except (TypeError, ValueError):
                return 400, {"ok": False, "error": "idle_minutes 需为数字"}
            if iv < 1 or iv > 120:
                return 400, {"ok": False, "error": "idle_minutes 需在 1~120 分钟"}
            cfg["idle_minutes"] = iv
        err = self._rules.apply_temp_curve(cfg, data, recover_max=100)
        if err:
            body, code = err
            return code, body
        if self._rules.save_disk_temp(cfg):
            return 200, {"ok": True, "config": cfg}
        return 500, {"ok": False, "error": "写配置失败"}

    def sys_temp_get(self) -> dict:
        """读取主板/CPU 温度控风扇配置 + 当前温度源读数 + 计算所得目标PWM"""
        cfg = self._rules.load_sys_temp()
        T = self._rules.read_sys_temp(cfg.get("source", "cpu"))
        target = self._rules.sys_temp_pwm(T, cfg) if T is not None else None
        return {
            "config": cfg,
            "source_temp": round(T, 1) if T is not None else None,
            "computed_pwm": round(target / 255 * 100) if target is not None else None,
            "computed_raw": target,
        }

    def sys_temp_set(self, data: dict) -> tuple:
        cfg = self._rules.load_sys_temp()
        if "source" in data:
            if data["source"] not in ("cpu", "mb"):
                return 400, {"ok": False, "error": "source 需为 cpu 或 mb"}
            cfg["source"] = data["source"]
        err = self._rules.apply_temp_curve(cfg, data, recover_max=120)
        if err:
            body, code = err
            return code, body
        if self._rules.save_sys_temp(cfg):
            return 200, {"ok": True, "config": cfg}
        return 500, {"ok": False, "error": "写配置失败"}

    def rules_get(self) -> dict:
        """读取逐风扇温控规则（含旧全局面板派生的默认）+ 每台风扇的温度源、实时温度、计算目标。
        is_custom：是否已存在用户逐风扇覆盖文件。"""
        rules = self._rules.effective_rules()
        dt = self._rules.load_disk_temp()
        _dt_eff = dt
        if not dt.get("disks"):
            _all_devs = self._c.list_all_disk_devs()
            if _all_devs:
                _dt_eff = dict(dt, disks=_all_devs)
        _need_disk_state = any(
            (r.get("source", "disk") == "disk") or "disk" in (r.get("source") or "") for r in rules.values())
        disk_all_idle, disk_T, disk_has = self._rules.disk_source_state(_dt_eff) if _need_disk_state else (False, None, bool(_dt_eff.get("disks")))
        cpu_T = self._rules.read_sys_temp("cpu")
        mb_T = self._rules.read_sys_temp("mb")
        fans_out = []
        for (hwmon, idx) in self._d.enumerate_fans():
            rk = "%s::%d" % (hwmon, idx)
            rule = rules.get(rk)
            lbl = self._labels.label_for(hwmon, idx)
            src = (rule or {}).get("source")
            if src == "cpu":
                T = cpu_T
            elif src == "mb":
                T = mb_T
            else:
                T = disk_T if rule else None
            computed = None
            if rule:
                raw = 0 if (src == "disk" and disk_all_idle) else self._rules.rule_pwm(T, rule)
                if raw is not None:
                    computed = round(raw / 255 * 100)
            fans_out.append({
                "hwmon": hwmon, "idx": idx, "key": rk,
                "name": lbl.get("name") or ("风扇%d" % idx),
                "hidden": bool(lbl.get("hidden")),
                "rule": rule,
                "source_temp": round(T, 1) if isinstance(T, (int, float)) else None,
                "computed_pwm": computed,
            })
        fans_out = self._labels.dedup_names(fans_out)
        return {
            "fans": fans_out,
            "is_custom": self._rules.load_rules_raw() is not None,
            "disk": {"disks": dt.get("disks", []), "has_disks": disk_has,
                     "all_idle": disk_all_idle, "temp": round(disk_T, 1) if isinstance(disk_T, (int, float)) else None},
            "cpu_temp": round(cpu_T, 1) if isinstance(cpu_T, (int, float)) else None,
            "mb_temp": round(mb_T, 1) if isinstance(mb_T, (int, float)) else None,
            "sources": list(self._rules.rule_sources()) + ["combo_max:cpu,mb", "combo_avg:cpu,mb"],
        }

    def rules_set(self, data: dict) -> tuple:
        """保存逐风扇温控规则覆盖。两种 body：
          整体覆盖：{"rules": {"<hwmon>::<idx>": {...}|null, ...}}
          单条设置：{"key": "<hwmon>::<idx>", "rule": {...}|null}
        rule=null 表示清除该风扇的自定义（回到全局默认/手动）。"""
        saved = self._rules.load_rules_raw() or {"rules": {}}
        cur = dict(saved.get("rules") or {})
        # 注意：保存温度联动规则【不再】清掉该风扇的手动覆盖（移除旧 _clear_manual_for_rule）。
        # 手动与曲线是两套独立状态：手动只是临时钉转速，曲线(fan_rules)始终保留；
        # 用户点「回到我的曲线」即恢复（见 api_fan_set mode=auto）。两者不再互斥。

        if "key" in data:
            k = data.get("key")
            if not isinstance(k, str) or "::" not in k:
                return 400, {"ok": False, "error": "key 需为 '<hwmon>::<idx>'"}
            clean, err = self._rules.validate_rule(data.get("rule"))
            if err:
                return 400, {"ok": False, "error": err}
            if clean is None:
                # 显式写入 null 占位：saved 里这条 key 不存在时 _derive_rules_from_legacy()
                # 仍会派生默认规则给该风扇，必须靠占位让 _effective_fan_rules() 的
                # `if r is None: rules.pop(k, None)` 把 legacy 默认也摘掉，关闭才真正生效。
                cur[k] = None
            else:
                cur[k] = clean
        elif "rules" in data:
            incoming = data.get("rules")
            if not isinstance(incoming, dict):
                return 400, {"ok": False, "error": "rules 需为对象"}
            for k, r in incoming.items():
                if not isinstance(k, str) or "::" not in k:
                    return 400, {"ok": False, "error": "规则 key 需为 '<hwmon>::<idx>'"}
                clean, err = self._rules.validate_rule(r)
                if err:
                    return 400, {"ok": False, "error": "%s: %s" % (k, err)}
                if clean is None:
                    # 同单条入口：必须写 null 占位，否则 legacy 派生默认会重新覆盖。
                    cur[k] = None
                else:
                    cur[k] = clean
        else:
            return 400, {"ok": False, "error": "缺少 key 或 rules"}
        if self._rules.save_rules({"rules": cur}):
            return 200, {"ok": True, "rules": cur}
        return 500, {"ok": False, "error": "写配置失败"}

    def fcs(self, data: dict) -> tuple:
        """永久禁用 / 恢复 FanControlServer。body: {"action": "disable"|"enable"}。
        disable = systemctl stop + disable（重启不复活）+ 持久化标志；enable = enable + start。"""
        action = data.get("action")
        if action == "disable":
            self._d.fcs_disable()
        elif action == "enable":
            self._d.fcs_enable()
        else:
            return 400, {"ok": False, "error": "action 需为 disable 或 enable"}
        # 操作完成后立即刷新缓存，让前端拿到最新状态
        return 200, {"ok": True, "status": self._d.fcs_status(clear=True)}

    def labels_get(self) -> dict:
        """返回用户标注的风扇名称/电压：key="hwmon::idx" -> {"name","voltage"}"""
        return self._labels.load()

    def labels_post(self, data: dict) -> tuple:
        """保存风扇标注（合并模式：仅覆盖传入的通道，保留其余，防止单条保存清空全部）。"""
        # 先读出现有标注,用于把改名/隐藏归并到同一 idx 的既有 key(跨 hwmon 漂移兜底,
        # 否则每次重启 hwmon 重排都新建孤立 key,改名在下次开机后像『没保存』)
        existing = self._labels.load()
        # 改动前名字快照：用于检测哪些 idx 被改名,以便连带重写历史库
        _old_names: dict = {}
        for _k, _v in existing.items():
            if "::" not in _k:
                continue
            try:
                _ii = int(_k.split("::", 1)[1])
            except (TypeError, ValueError):
                continue
            _nm = (_v or {}).get("name")
            if _nm:
                _old_names[_ii] = _nm
        incoming = {}
        for k, v in data.items():
            if not isinstance(k, str) or "::" not in k:
                continue
            hwmon, idx = k.split("::", 1)
            # 安全：仅允许本机 hwmon 路径 + 合法 idx，防止注入
            if not hwmon.startswith("/sys/class/hwmon/hwmon"):
                continue
            try:
                idx_i = int(idx)
            except (TypeError, ValueError):
                continue
            if not isinstance(v, dict):
                continue
            name = str(v.get("name", ""))[:40]
            volt = v.get("voltage", "")
            if volt not in self._labels.volt_allowed():
                volt = "未知"
            entry = {}
            if name:
                entry["name"] = name
            if volt != "未知":
                entry["voltage"] = volt
            if v.get("hidden"):
                entry["hidden"] = True   # 隐藏无风扇的幽灵通道（可恢复）
            if v.get("no_tach"):
                # 用户标注「这把风扇没有转速反馈线」（2 针风扇 / 分线器副扇 / 转速线未接）：
                # 风扇本身在转，只是主板永远读不到 rpm。标了之后转速栏不再显示刺眼的 0，
                # 也不会被误判成「停转/空通道」。
                entry["no_tach"] = True
            # 空标注（name 空且无 hidden/no_tach）→ 视为取消该 idx 的标注，删除既有 key
            if not entry:
                canon = self._labels.canon_key(existing, idx_i)
                if canon:
                    incoming[canon] = None
                continue
            # 同一 idx 的既有标注可能落在别的 hwmon 路径下(hwmon 漂移),写入时归并到那条 key,
            # 避免重复建 key;没有既有 key 才用本次传入的(当前 hwmon 路径)。
            canon = self._labels.canon_key(existing, idx_i) or k
            incoming[canon] = entry
        # 合并现有标注，而非整体覆盖：防御前端只传单条导致全量清空
        for k, v in incoming.items():
            if v is None:
                existing.pop(k, None)
            else:
                existing[k] = v
        self._labels.replicate(existing)   # 别名同步：同名标注通道改名/隐藏联动（huhaibo820 #1）
        self._labels.consolidate(existing)  # 保存前再归并一次：清除漂移产生的重复 idx key
        if self._labels.save(existing):
            # 保存成功后立即让系统总览缓存失效，风扇卡片无需强制刷新即可显示新名字
            self._c.invalidate_system_cache()
            # 检测改名：把历史库里该 idx 的所有旧样本名字也改成新名(连带改名,无残留,不丢历史)
            _renames = []
            for _k, _v in existing.items():
                if "::" not in _k:
                    continue
                try:
                    _ii = int(_k.split("::", 1)[1])
                except (TypeError, ValueError):
                    continue
                _nm = (_v or {}).get("name")
                if _nm and _old_names.get(_ii) != _nm:
                    _renames.append((_ii, _nm, _old_names.get(_ii)))
            if _renames:
                def _rename_history_bg():
                    for (_i, _nn, _on) in _renames:
                        self._history.rename_fan(_i, _nn, _on)
                threading.Thread(target=_rename_history_bg, daemon=True).start()
            return 200, {"ok": True}
        return 500, {"ok": False, "error": "save failed"}

    def history(self, rng: str) -> tuple:
        """返回每台风扇的转速/占空比/接管状态历史（每 30s 一点，保留 30 天）。供风扇问题排查复盘。"""
        try:
            secs = _RANGE_SECS.get(rng, 86400)
            end = int(time.time())
            start = end - secs
            rows = self._history.fan_points(start)
            points = []
            for ts, detail in rows:
                try:
                    fl = json.loads(detail) if detail else []
                except Exception:
                    fl = []
                points.append({"ts": ts * 1000, "fans": fl})
            return 200, {"range": rng, "points": points}
        except Exception as e:
            return 200, {"error": str(e), "points": []}
