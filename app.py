#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nasdeck 应用入口（薄门面）。

原 11,251 行单文件 Flask 实现已拆分至 backend/ 包（方案与铁律见 docs/重构计划.md，
分阶段进度见 docs/开发计划.md）。本文件保留三个职责：

1. `app`：create_app() 装配出的 FastAPI 实例 —— cmd/main 仍以
   `$PYTHON "$APP_DIR/app.py"` 启动、/app/app.py 软链自愈，文件名与路径不变；
2. re-export：scripts/test_app_parsers.py 以 `import app` 方式引用的符号清单；
3. `__main__` 入口 → backend.serve.main()（启动序列：错误圈 → 配置迁移 →
   历史库 → 风扇模式恢复 → 后台线程 → uvicorn 监听）。
"""
import glob  # noqa: F401  (测试 monkeypatch 目标：app.glob / FakeGlob)
import os    # noqa: F401  (测试 monkeypatch 目标：app.os.path.exists)

from backend.infrastructure.collectors.cputemp import (_mb_temp_from_sensors, _parse_cpu_temp,  # noqa: F401
                                        _parse_mb_temp, _parse_sensors_all)
from backend.infrastructure.collectors.disks import get_disks  # noqa: F401
from backend.infrastructure.collectors.dockermod import (_docker_size_to_bytes,  # noqa: F401
                                          _parse_docker_pct, _split_netio)
from backend.infrastructure.collectors.raid import (_parse_ctrl_temp, _parse_roc_temp,  # noqa: F401
                                     _resolve_brand_model,
                                     detect_storage_controllers,
                                     disk_brand_and_feature)
from backend.infrastructure.collectors.smart import parse_nvme_smart  # noqa: F401
from backend.infrastructure.fans.control import get_fan_status  # noqa: F401
from backend.infrastructure.fans.enumerate import _enumerate_fans, _select_temp_fans  # noqa: F401
from backend.infrastructure.fans.fcs import (_fcs_disable, _fcs_enable, _fcs_status,  # noqa: F401
                              _fan_start_ext_service, _fan_stop_ext_service)
from backend.infrastructure.fans.rules import _fan_curve_pwm  # noqa: F401
from backend.infrastructure.health.alerts import _evaluate_alerts  # noqa: F401
from backend.infrastructure.health.report import _render_report_html, build_health_report  # noqa: F401
from backend.presentation.app import create_app
from backend.presentation.staticpages import _vue_missing_page  # noqa: F401

app = create_app()

if __name__ == "__main__":
    from backend.serve import main
    main()
