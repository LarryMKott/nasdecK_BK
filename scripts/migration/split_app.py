#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性迁移工具：把单文件 app.py 按顶层符号拆分到 backend/ 包。

原理（见 docs/重构计划.md）：
- AST 解析顶层语句，每条语句连同其前导注释/空行整体搬迁，杜绝切进函数体；
- 路由函数、Flask 初始化、import 期副作用语句（线程启动、stderr tee、迁移调用）
  不在自动搬迁范围（阶段 5/6 手工改写）；
- 生成后由 pyflakes 迭代补 import（见 fix_imports.py）。

用法： .venv/bin/python scripts/migration/split_app.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "app.py"
OUT = ROOT / "backend"

# name → 目标模块（backend/ 下相对路径，不含 .py）
MAPPING = {}

def _m(module, *names):
    for n in names:
        MAPPING[n] = module

# ---- 阶段 1：无依赖层（少量手工模块之外的自动搬迁项）----
_m("shell", "log", "read_file", "_safe_token", "_run_raw", "run_cmd", "sudo_cmd",
   "_find_storcli", "STORCLI", "SMARTCTL", "BADBLOCKS", "SENSORS", "DMIDECODE")
_m("jsonstore", "_load_json_file", "_save_json_file")
_m("version", "_app_version", "APP_VERSION", "_fnos_version", "_VERSION_CHECK",
   "_VERSION_CHECK_TTL", "_VERSION_REPO_URL", "_check_latest_version")
_m("caching", "_ttl_cache")

# ---- 阶段 2：采集层 ----
_m("collectors.cpuusage", "_cpu_snap", "_cpu_sample_1s", "get_cpu_usage")
_m("collectors.cputemp", "_mb_temp_from_sensors", "_parse_cpu_temp", "_parse_mb_temp", "_parse_sensors_all",
   "_CPU_TEMP_EMA", "_smooth_cpu_temp", "_temp_snapshot_read", "_temp_collect_loop")
_m("collectors.raid", "detect_storage_controllers", "_storcli_size_to_decimal",
   "_resolve_brand_model", "disk_brand_and_feature", "_smart_rpm_by_serial",
   "_parse_roc_temp", "_parse_ctrl_temp", "_parse_cachevault",
   "_parse_vds_from_topology", "_parse_vd_cache_policies", "_probe_locate_support",
   "_pd_health_from_storcli", "_parse_pd_show_all", "get_raid_card")
_m("collectors.smart", "parse_sas_smart", "parse_ata_smart", "parse_nvme_smart")
_m("collectors.disks", "get_disk_temps", "_DISK_TEMP_CACHE", "_DISK_TEMP_TTL",
   "_DISK_TEMP_LOCK", "get_disk_temps_cached", "_DISK_IO_CACHE",
   "_DISK_FRIENDLY_CACHE", "_DISK_FRIENDLY_TTL", "_SYSTEM_DISK_DEV",
   "_detect_system_disk_dev", "_short_size", "_short_disk_name",
   "_disk_friendly_info", "_list_all_disk_devs", "_disk_idle", "_disk_is_ssd",
   "DISK_TEST_LOCK", "DISK_TEST_JOBS", "DISK_TEST_HISTORY_FILE",
   "DISK_TEST_HISTORY_MAX", "_load_disk_test_history", "_append_disk_test_history",
   "_is_standalone_disk", "_SMART_HISTORY_IDS", "_smart_short_attrs",
   "_set_disk_test_done", "_abort_smart_long", "_start_smart_long",
   "_smart_long_worker", "_estimate_badblocks_eta", "_start_badblocks",
   "_badblocks_worker", "_abort_badblocks", "_DISKS_CACHE", "_DISKS_TTL",
   "_DISKS_LOCK", "get_disks", "_collect_disks_full")
_m("collectors.board", "_mem_brand_cn", "get_chipset", "_amd_chipset_label",
   "get_board", "_clean_mfr", "_real_serial", "_spd_mfg_date_and_serial",
   "_mem_ecc_flag", "get_memory_edac", "get_memory_from_decodedimms",
   "get_memory_modules", "_TEMP_NAME_ZH", "_temp_name_zh")
_m("collectors.system", "_RAPL_BASE", "_rapl_read_energy", "_rapl_energy_max",
   "get_rapl_power", "get_network_nics", "_SYSTEM_CACHE", "_SYSTEM_TTL",
   "_SYSTEM_LOCK", "get_system", "_collect_system_full", "format_uptime", "fmt_kb")
_m("collectors.storage", "CLOUD_MOUNT_INFO", "_cloud_mount_map", "get_storage",
   "get_volume_map", "fmt_blocks")
_m("collectors.dockermod", "_listening_ports_in_netns", "_listening_ports_for_pids",
   "_container_pids", "_detect_ports", "_cn_status", "_parse_docker_pct",
   "_host_cpu_threads", "_docker_size_to_bytes", "_split_netio",
   "_DOCKER_NET_PREV", "_docker_net_rate", "get_docker")
_m("collectors.ports", "_docker_port_map", "_DOCKER_IFACE_RE", "_local_net_zones",
   "_addr_zone", "_PORT_PROTECTED", "_SERVICE_DESC", "_RPC_PORT_CACHE",
   "_rpc_port_map", "_RPC_DESC", "_APP_IDS_CACHE", "_APP_IDS_TTL",
   "_APP_NAMES_CACHE", "_RUNTIME_APP_IDS", "_app_ids", "_app_display_names",
   "_docker_id_map", "_CGROUP_CID_RE", "_cgroup_container", "_APP_ICON_PREF",
   "_APP_ICON_EXTS", "_ICON_MIME", "_app_icon_file", "_PID_FS", "_pid_info",
   "_app_of_pid", "_expand_pid_chain", "_app_friendly", "_proc_comm",
   "get_listening_ports")
_m("collectors.metrics", "_METRICS_LOCK", "_metrics_prev", "_metrics_cur",
   "_CPU_POWER_EMA", "_read_net_bytes", "_read_disk_stats", "_DISK_LAST_IO",
   "_DISK_STANDBY_IDLE_MIN", "_read_rapl_energy", "get_realtime_metrics")
_m("collectors.gpu", "_GPU_IDENT_CACHE", "_GPU_LIVE_CACHE", "_clean_gpu_name",
   "_gpu_ident_list", "_gpu_temp_from_sysfs_live", "_amd_gpu_busy", "_read_drm_attr",
   "_IGPU_TOP_CACHE", "_IGPU_TOP_TTL", "_intel_igpu_top_sample", "_intel_igpu_util",
   "_gpu_memory_bytes", "_intel_igpu_vram_bytes", "_sample_gpu_live", "_get_gpu_live")
_m("collectors.wol", "_wol_cfg_path", "_wol_load_cfg", "_wol_save_cfg",
   "_wol_phy_name", "_wol_read_modes", "_wol_hw_supported", "_wol_apply",
   "_wol_reapply_on_start")

# ---- 阶段 3：风扇域 ----
_m("fans.hwmon", "_fan_read_raw", "_fan_write_raw", "_FAN_RELEASE_IDLE_PWM",
   "_fan_release_to_idle", "_fan_set_enable", "_fan_release_auto",
   "_fan_read_pwm_mode", "_fan_set_pwm_mode", "_FAN_ENABLE_CACHE")
_m("fans.fcs", "_fan_ext_service_running", "_FCS_TAKEN", "FCS_STATE_FILE",
   "_FCS_OP_LOCK", "_fcs_disabled", "_set_fcs_disabled", "_fcs_installed_state",
   "_fcs_status", "_FCS_DIAG_HARD", "_fcs_status_hard", "_FCS_STATUS_CACHE",
   "_FCS_STATUS_TTL", "_fcs_status_refresh_loop", "_fcs_status_cached",
   "_fcs_has_board_config", "_FCS_BOARD_CFG_CACHE", "_fcs_board_cfg_cached",
   "_fcs_running_cached", "_fcs_installed_cached", "_fan_stop_ext_service",
   "_fan_start_ext_service", "_fcs_disable", "_fcs_enable")
_m("fans.modes", "FAN_MODE_FILE", "_load_fan_modes", "_save_fan_modes",
   "_save_fan_mode", "_restore_fan_modes", "FAN_CONTROL_CFG",
   "_FAN_CTRL_ENABLED", "_load_fan_control_enabled")
_m("fans.enumerate", "_FAN_SNAP", "_FAN_ENUM_CACHE", "_enumerate_fans", "_select_temp_fans")
_m("fans.labels", "FAN_LABELS_FILE", "_FAN_VOLT_ALLOWED", "_load_fan_labels",
   "_save_fan_labels", "_consolidate_fan_labels", "_fan_label_for",
   "_canon_label_key", "_fan_alias_map", "_fan_alias_members_of",
   "_apply_fan_aliases", "_replicate_aliases", "_dedup_fan_names",
   "_read_fan_drivers")
_m("fans.rules", "_apply_temp_curve", "_fan_auto_pwm", "_fan_smooth_step",
   "FAN_DISK_TEMP_FILE", "_load_fan_disk_temp", "_save_fan_disk_temp",
   "FAN_SYS_TEMP_FILE", "_load_fan_sys_temp", "_save_fan_sys_temp",
   "_fan_read_sys_temp", "_fan_curve_pwm", "_fan_sys_temp_pwm", "_st_engaged",
   "_fan_sys_temp_decision", "_fan_disk_temp_decision", "_dt_engaged",
   "FAN_RULES_FILE", "_FAN_RULE_SOURCES",
   "_load_fan_rules_raw", "_save_fan_rules", "_rule_from_cfg",
   "_derive_rules_from_legacy", "_effective_fan_rules", "_disk_source_state",
   "_fan_rule_pwm", "_FAN_ENGAGED", "_fan_rule_decision", "_resolve_rule_temp",
   "NVME_START_TEMP", "NVME_FULL_TEMP", "_nvme_temp_as_hdd",
   "_all_monitored_idle", "_disk_source_max_temp", "_fan_disk_temp_pwm",
   "_validate_fan_rule")
_m("fans.control", "_fan_cpu_temp_cached", "fan_smooth_loop", "_fan_smooth_runner",
   "_fan_ensure_all_claimed", "get_fan_status")

# ---- 阶段 4：历史/健康/调度 ----
_m("history.db", "_DB_PATH", "_db_lock", "_db_last_write", "_init_history_db",
   "_write_history_sample", "_rename_history_fan")
_m("health.diskhealth", "_DEFECT_COUNTERS", "_WEAR_COUNTERS", "_TREND_COUNTERS",
   "_PD_BAD_STATES", "_HEALTH_META", "_grade_disk_health",
   "DISK_HEALTH_HIST_FILE", "DISK_HEALTH_HIST_MAX", "DISK_HEALTH_KEEP_DAYS",
   "DISK_HEALTH_INTERVAL", "_DISK_HEALTH_LOCK", "_DISK_HEALTH_CACHE",
   "_health_hist_rows", "_health_hist_last_ts", "_health_hist_prev",
   "_health_hist_append", "_raid_pd_by_sn", "_health_key_for", "_DISK_NAME_MAX",
   "_disk_names_path", "_disk_names_load", "_disk_name_clean",
   "_disk_health_snapshot_now", "_health_trend_for", "disk_health_loop",
   "_double_size", "_merge_dual_actuator", "_merge_disk_group",
   "_enrich_disk_channels", "_MERGE_MAX_KEYS", "_GRADE_RANK")
_m("health.alerts", "ALERTS_FILE", "_NOTIFY_LOG", "_load_alerts", "_save_alerts",
   "_evaluate_alerts", "_notify_log", "_send_telegram", "_send_bark",
   "_send_email", "_dispatch_notifications")
_m("health.report", "_read_app_log", "_parse_log_entries", "_mask_serial",
   "_mask_ip", "_mask_mac", "_redact_report", "build_health_report",
   "build_diagnostics", "render_diagnostics_text", "_render_report_html",
   "_debug_log_tail")
_m("schedule", "_CC_SCHEDULE_FILE", "_cc_sched_last_check", "_load_cc_schedule",
   "_save_cc_schedule", "_check_cc_schedule", "_raid_cc_vnum")

# ---- 阶段 6：路由区帮手（先归位，路由函数本体阶段 6 手工迁移）----
_m("web.staticpages", "ui_images", "_vue_missing_page", "_serve_vue_app", "index",
   "vue_pilot", "_MANUAL_CSS", "_MANUAL_CSS_EMBED", "_md_inline",
   "_render_markdown_stream", "manual")
_m("web.routers.helpers", "_panel_time", "get_live_mem", "_DISKS_META_CACHE",
   "_DISKS_META_TTL", "_DISKS_META_LOCK", "_disks_meta_map")

# ---- 不自动搬迁（手工处理）----
SKIP_NAMES = {
    "app",                    # Flask 实例（阶段 6 create_app）
    "_parse_ver",             # 双定义，version.py 手工拆名
    "get_gateway_user", "require_admin",   # gateway.py 手工改写为 FastAPI 依赖
    "APP_DIR", "LEGACY_APPATA", "_config_dir", "_migrate_legacy_configs",  # paths.py 手工
    "_ERR_RING", "_StderrTee",             # errring.py 手工
    "_TEMP_SNAP", "_TEMP_SNAP_LOCK", "_CPU_USAGE_CACHE", "_CPU_USAGE_LOCK",
    "FAN_LOCK", "FAN_TARGETS",             # state.py 手工
    "metrics_collect_loop", "_warmup_caches",  # daemon.py 手工组装
    "_serve_gateway",                      # serve.py 手工改写 uvicorn
    "api_errors_clear", "api_errors_test", "api_me",  # 头部 3 路由，阶段 6
}
SKIP_LINENOS = set()  # 校验用：预期被跳过的语句行号会打印出来人工复核

# 线程句柄与 import 期副作用语句（daemon/serve 阶段重组）
THREAD_VARS = {"_fan_thread", "_metrics_thread", "_temp_thread", "_warmup_thread",
               "_disk_health_thread"}


def stmt_names(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def main():
    src = SRC.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)

    # 计算每条顶层语句的搬迁区间：上一条结束行+1 ～ 本条结束行（吸收前导注释/空行）
    spans = []  # (start_line, end_line, node)
    prev_end = 0
    for node in tree.body:
        spans.append((prev_end + 1, node.end_lineno, node))
        prev_end = node.end_lineno

    buckets = {}  # module -> list[str] 文本片段
    order = {}
    unassigned = []
    for start, end, node in spans:
        names = stmt_names(node)
        text = "".join(lines[start - 1:end])
        targets = set()
        for n in names:
            if n in SKIP_NAMES or n in THREAD_VARS:
                continue
            if n in MAPPING:
                targets.add(MAPPING[n])
            else:
                unassigned.append((start, n or type(node).__name__, text.splitlines()[0][:60]))
        if not targets:
            continue
        if len(targets) > 1:
            # 同一语句的多个目标分别属于不同模块：按名字逐个拆不了（语句级整体），
            # 归入第一个目标并告警
            print(f"警告: L{start} 多目标跨模块 {sorted(targets)}，归入 {sorted(targets)[0]}")
        mod = sorted(targets)[0]
        buckets.setdefault(mod, [])
        buckets[mod].append((start, text))

    for mod, frags in buckets.items():
        path = OUT / (mod.replace(".", "/") + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）\n"
        body += "".join(t for _, t in sorted(frags))
        path.write_text(body, encoding="utf-8")
        print(f"写出 {path.relative_to(ROOT)}：{sum(t.count(chr(10)) for _, t in frags)} 行")

    # 各包 __init__.py
    for pkg in ("backend", "backend.collectors", "backend.fans", "backend.history",
                "backend.health", "backend.web", "backend.web.routers"):
        p = OUT / pkg.replace(".", "/") / "__init__.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")

    if unassigned:
        print(f"\n未映射符号 {len(unassigned)} 个：")
        for line, name, head in unassigned:
            print(f"  L{line:6} {name:30} {head}")
    else:
        print("\n全部符号已映射")


if __name__ == "__main__":
    sys.exit(main())
