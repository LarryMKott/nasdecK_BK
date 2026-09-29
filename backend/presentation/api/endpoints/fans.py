# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
# v2.4 阶段 3 批 B：编排收敛至 application.usecases.fan_control_usecase，
# 端点只做 解析→用例→透传。
from fastapi import APIRouter, Depends, Request

from backend.container import get_container
from backend.infrastructure.gateway import require_admin
from backend.presentation.api.dependencies import _jr, json_body_as
from backend.presentation.schemas import FanSetRequest, FanPwmModeRequest, FanControlRequest, FanDiskTempConfigRequest, FanSysTempConfigRequest, FanRulesRequest, FanFcsRequest, FanLabelsRequest

router = APIRouter()



@router.post("/api/fan/set")
def api_fan_set(request: Request, payload: dict = Depends(json_body_as(FanSetRequest)), _admin = Depends(require_admin)):
    """设置风扇转速：设目标 PWM（后台缓变线程平滑过渡）或恢复自动控温"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    status, body = get_container().fan_uc.set(data)
    return _jr(body, status)

@router.post("/api/fan/pwm_mode")
def api_fan_pwm_mode(request: Request, payload: dict = Depends(json_body_as(FanPwmModeRequest)), _admin = Depends(require_admin)):
    """切换风扇接口的调速信号类型：PWM(4pin 脉宽) ↔ DC(3pin 直流电压)。

    背景：3pin 风扇插在被设成 PWM 的接口上会一直全速——因为它没有 PWM 线，只认电压。
    多数主板 BIOS 里能给每个接口单独选 PWM/DC，Linux 下对应 pwmN_mode 寄存器。
    但**能不能改由主板硬件决定**：部分接口只焊了 PWM 电路，写 DC 会被内核直接拒绝，
    这种情况只能进 BIOS 或换 4pin 风扇，属主板限制而非软件问题。
    """
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    status, body = get_container().fan_uc.pwm_mode(data)
    return _jr(body, status)

@router.get("/api/fan/status")
def api_fan_status():
    """轻量风扇状态：供前端高频轮询，实时显示转速/当前占空比/目标（常驻线程 2s tick）"""
    return _jr(get_container().fan_uc.status())

@router.get("/api/fan/control")
def api_fan_control_get():
    """风扇接管总开关状态。"""
    return _jr(get_container().fan_uc.control_get())

@router.post("/api/fan/control")
def api_fan_control_set(request: Request, payload: dict = Depends(json_body_as(FanControlRequest)), _admin = Depends(require_admin)):
    """开关风扇接管：关闭后 nasdeck 不再根据温度自动调速。若飞牛自带风扇服务已配置，
    会交还它接管；否则保持当前转速、不再写 PWM。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    status, body = get_container().fan_uc.control_set(data)
    return _jr(body, status)

@router.get("/api/fan/temps")
def api_fan_temps():
    """温度统一快照：CPU/主板/各硬盘/全量测点/阵列卡，供前端「当前温度」卡与温度墙高频展示。
    全部数据来自后台采集循环的 _TEMP_SNAP（~2s 刷新），不再各自采样。"""
    return _jr(get_container().fan_uc.temps())

@router.get("/api/fan/disk_temp")
def api_fan_disk_temp_get():
    """读取硬盘温度控风扇配置 + 实时监控盘温度/休眠 + 计算所得目标PWM"""
    return _jr(get_container().fan_uc.disk_temp_get())

@router.post("/api/fan/disk_temp")
def api_fan_disk_temp_set(request: Request, payload: dict = Depends(json_body_as(FanDiskTempConfigRequest)), _admin = Depends(require_admin)):
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    status, body = get_container().fan_uc.disk_temp_set(data)
    return _jr(body, status)

@router.get("/api/fan/sys_temp")
def api_fan_sys_temp_get():
    """读取主板/CPU 温度控风扇配置 + 当前温度源读数 + 计算所得目标PWM"""
    return _jr(get_container().fan_uc.sys_temp_get())

@router.post("/api/fan/sys_temp")
def api_fan_sys_temp_set(request: Request, payload: dict = Depends(json_body_as(FanSysTempConfigRequest)), _admin = Depends(require_admin)):
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    status, body = get_container().fan_uc.sys_temp_set(data)
    return _jr(body, status)

@router.get("/api/fan/rules")
def api_fan_rules_get():
    """读取逐风扇温控规则（含旧全局面板派生的默认）+ 每台风扇的温度源、实时温度、计算目标。
    is_custom：是否已存在用户逐风扇覆盖文件。"""
    return _jr(get_container().fan_uc.rules_get())

@router.post("/api/fan/rules")
def api_fan_rules_set(request: Request, payload: dict = Depends(json_body_as(FanRulesRequest)), _admin = Depends(require_admin)):
    """保存逐风扇温控规则覆盖。两种 body：
      整体覆盖：{"rules": {"<hwmon>::<idx>": {...}|null, ...}}
      单条设置：{"key": "<hwmon>::<idx>", "rule": {...}|null}
    rule=null 表示清除该风扇的自定义（回到全局默认/手动）。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    status, body = get_container().fan_uc.rules_set(data)
    return _jr(body, status)

@router.post("/api/fan/fcs")
def api_fan_fcs_post(request: Request, payload: dict = Depends(json_body_as(FanFcsRequest)), _admin = Depends(require_admin)):
    """永久禁用 / 恢复 FanControlServer。body: {"action": "disable"|"enable"}。
    disable = systemctl stop + disable（重启不复活）+ 持久化标志；enable = enable + start。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    status, body = get_container().fan_uc.fcs(data)
    return _jr(body, status)

@router.get("/api/fan/labels")
def api_fan_labels_get():
    """返回用户标注的风扇名称/电压：key="hwmon::idx" -> {"name","voltage"}"""
    return _jr(get_container().fan_uc.labels_get())

@router.get("/api/fan/history")
def api_fan_history(request: Request):
    """返回每台风扇的转速/占空比/接管状态历史（每 30s 一点，保留 30 天）。供风扇问题排查复盘。"""
    rng = request.query_params.get("range", "24h")
    status, body = get_container().fan_uc.history(rng)
    return _jr(body, status)

@router.post("/api/fan/labels")
def api_fan_labels_post(request: Request, payload: dict = Depends(json_body_as(FanLabelsRequest)), _admin = Depends(require_admin)):
    """保存风扇标注（合并模式：仅覆盖传入的通道，保留其余，防止单条保存清空全部）。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    status, body = get_container().fan_uc.labels_post(data)
    return _jr(body, status)
