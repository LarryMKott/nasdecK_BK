# -*- coding: utf-8 -*-
"""Pydantic v2 请求模型（硬性规范 #4）：全部写接口的入参契约。

校验分两层（兼容性优先的取舍，见 docs/FastAPI迁移说明.md）：
- 模型层（这里）：**结构校验** —— body 必须是 JSON 对象、字段名/形状可文档化进
  OpenAPI；字段值统一 Any，不做类型强校验。原因：路由内部保留了原 Flask 的
  字段级校验（缺参数/类型错 → 400 + 具体中文文案），若模型层强校验会把这些
  错误变成 422 通用文案，前端错误提示文案即产生契约漂移。
- 路由层（routers/）：原有 `data.get(...)` + 手工校验逐字保留，400 行为不变。

所有模型一律 `model_config = ConfigDict(extra="allow")` 且字段全可选 ——
不拒绝历史前端可发送的任何载荷。
"""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Lenient(BaseModel):
    """全模型基类：extra=allow + 字段全可选（文档用 Field description 描述契约）。"""
    model_config = ConfigDict(extra="allow")


# ---- basic ----
class ErrorsClearRequest(_Lenient):
    """清空错误历史圈。无必填字段。"""

class ErrorsTestRequest(_Lenient):
    """写入一条测试错误（验证错误记录功能）。无必填字段。"""


# ---- fans ----
class FanSetRequest(_Lenient):
    """手动设置风扇转速。"""
    hwmon: Any = Field(None, description="hwmon 路径，如 /sys/class/hwmon/hwmon4")
    idx: Any = Field(None, description="风扇通道号（pwmN 的 N）")
    mode: Any = Field(None, description="manual=定值手动 / auto=按温控曲线自动")
    pwm: Any = Field(None, description="manual 模式的占空比 0-255")

class FanPwmModeRequest(_Lenient):
    """设置 pwm_enable 模式。"""
    hwmon: Any = Field(None, description="hwmon 路径")
    idx: Any = Field(None, description="通道号")
    pwm_mode: Any = Field(None, description="0=无控 1=手动 PWM 2=自动（交还主板）")

class FanControlRequest(_Lenient):
    """风扇接管总开关。"""
    enabled: Any = Field(None, description="true=接管软件控速 / false=停手交还")

class FanDiskTempConfigRequest(_Lenient):
    """硬盘温度控速配置（整包透传存储，字段见面板：源硬盘集合/开转/满转温度等）。"""
    config: Any = Field(None, description="完整配置对象（透传持久化）")

class FanSysTempConfigRequest(_Lenient):
    """主板/CPU 温度控速配置（整包透传存储）。"""
    config: Any = Field(None, description="完整配置对象（透传持久化）")

class FanRulesRequest(_Lenient):
    """逐风扇温度联动规则集（整包替换存储）。"""
    rules: Any = Field(None, description="规则数组，每条含 fan/temp source/曲线点等")
    key: Any = Field(None, description="单条操作时的规则键")
    rule: Any = Field(None, description="单条操作时的规则对象")

class FanFcsRequest(_Lenient):
    """FanControlServer（飞牛原生风扇服务）接管动作。"""
    action: Any = Field(None, description="start / stop / disable / enable")

class FanLabelsRequest(_Lenient):
    """风扇自定义命名（按 (hwmon, idx) 键）。"""
    labels: Any = Field(None, description="{\"<hwmon>|<idx>\": \"自定义名\"}")


# ---- disks ----
class DiskRenameRequest(_Lenient):
    """硬盘自定义命名。"""
    dev: Any = Field(None, description="/dev/sdX 或阵列卡槽位标识")
    name: Any = Field(None, description="自定义名（空串=清除）")
    key: Any = Field(None, description="别名键（部分入口按 key 命名）")

class DiskSelftestRequest(_Lenient):
    """启动硬盘自检（A=smart short / B=smart long / C=badblocks 全盘）。"""
    dev: Any = Field(None, description="盘符")
    type: Any = Field(None, description="A / B / C")
    confirm: Any = Field(None, description="C 档破坏性写测需二次确认 true")

class DiskSelftestAbortRequest(_Lenient):
    """中止进行中的自检。"""
    dev: Any = Field(None, description="盘符")


# ---- raid ----
class RaidLocateRequest(_Lenient):
    """物理盘定位灯。"""
    action: Any = Field(None, description="on / off")
    slot: Any = Field(None, description="槽位号")

class RaidCcRequest(_Lenient):
    """一致性检查操作。"""
    action: Any = Field(None, description="start / stop / pause / resume")
    vd: Any = Field(None, description="虚拟盘编号")
    force: Any = Field(None, description="忽略低速警告强制执行")

class RaidCcScheduleRequest(_Lenient):
    """一致性检查定时调度配置（整包透传存储）。"""
    config: Any = Field(None, description="调度配置对象")

class RaidHotspareRequest(_Lenient):
    """热备盘操作。"""
    action: Any = Field(None, description="add / delete")
    slot: Any = Field(None, description="槽位号")

class RaidCopybackRequest(_Lenient):
    """回拷开关。"""
    action: Any = Field(None, description="on / off")
    slot: Any = Field(None, description="槽位号")


# ---- network / ports ----
class NetworkWolRequest(_Lenient):
    """网络唤醒开关。"""
    name: Any = Field(None, description="网卡名")
    enable: Any = Field(None, description="true=开 WOL / false=关")

class PortsReleaseRequest(_Lenient):
    """释放端口占用（终止占用进程）。"""
    pid: Any = Field(None, description="目标进程 PID（路由内做关键进程保护校验）")


# ---- alerts ----
class AlertsConfigRequest(_Lenient):
    """告警配置（整包透传存储）。"""
    config: Any = Field(None, description="完整告警配置对象（阈值/通知渠道等）")
    level_channels: Any = Field(None, description="告警级别 → 通知渠道映射")

class AlertsTestRequest(_Lenient):
    """发送测试通知。"""
    channel: Any = Field(None, description="telegram / bark / email")


# ---- 路径 → 请求模型映射（create_app 注入 OpenAPI requestBody 用）----
OPENAPI_REQUEST_MODELS = {
    "/api/errors/clear": ErrorsClearRequest,
    "/api/errors/test": ErrorsTestRequest,
    "/api/fan/set": FanSetRequest,
    "/api/fan/pwm_mode": FanPwmModeRequest,
    "/api/fan/control": FanControlRequest,
    "/api/fan/disk_temp": FanDiskTempConfigRequest,
    "/api/fan/sys_temp": FanSysTempConfigRequest,
    "/api/fan/rules": FanRulesRequest,
    "/api/fan/fcs": FanFcsRequest,
    "/api/fan/labels": FanLabelsRequest,
    "/api/disks/rename": DiskRenameRequest,
    "/api/disks/selftest": DiskSelftestRequest,
    "/api/disks/selftest/abort": DiskSelftestAbortRequest,
    "/api/raid/locate": RaidLocateRequest,
    "/api/raid/cc": RaidCcRequest,
    "/api/raid/cc/schedule": RaidCcScheduleRequest,
    "/api/raid/hotspare": RaidHotspareRequest,
    "/api/raid/copyback": RaidCopybackRequest,
    "/api/network/wol": NetworkWolRequest,
    "/api/ports/release": PortsReleaseRequest,
    "/api/alerts/config": AlertsConfigRequest,
    "/api/alerts/test": AlertsTestRequest,
}
