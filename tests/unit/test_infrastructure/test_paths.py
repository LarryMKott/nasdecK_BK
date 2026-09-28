# -*- coding: utf-8 -*-
"""paths.py 锚点契约：APP_DIR 必须锚定应用根（backend/ 的父目录）。

阶段2a 目录落位时 REPO_ROOT 漏随迁（少三级 dirname），APP_DIR 曾指到
backend/infrastructure：/ 与 /vue 静态直出 500、version appid 误判、
debug.log/配置兜底目录整体偏移。本测试锁死锚点契约，防再次随迁漏改。
"""
import os


def test_app_dir_anchors_at_repo_root():
    from backend.infrastructure.config.paths import APP_DIR

    # 应用根下应有 backend/ 包与单文件入口 app.py（仓库与 FPK 部署形态同构）
    assert os.path.isdir(os.path.join(APP_DIR, "backend"))
    assert os.path.isfile(os.path.join(APP_DIR, "app.py"))


def test_vue_template_dir_exists():
    from backend.infrastructure.config.paths import APP_DIR

    # 静态直出目录：templates/vue/index.html（构建产物随仓库提交）
    assert os.path.isdir(os.path.join(APP_DIR, "templates", "vue"))
