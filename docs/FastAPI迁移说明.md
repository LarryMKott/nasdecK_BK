# nasdash FastAPI 迁移说明

> v2.3.3 单文件 Flask（app.py 11,251 行）→ FastAPI 模块化后端 的完整迁移记录。
> 方案细节：[`docs/重构计划.md`](重构计划.md)；分阶段进度：[`docs/开发计划.md`](开发计划.md)。

## 一、项目目录结构

```
nasdash/
├── app.py                    # 薄门面（约 60 行）：app = create_app() + 测试符号 re-export + __main__
├── requirements.txt          # 运行依赖：fastapi>=0.100 / uvicorn / sqlalchemy>=2.0
├── manifest / build.sh / cmd/ / ui/ / config/ / wizard/   # 飞牛 FPK 包层（未改动，除 install_callback 依赖）
├── frontend-vue/             # Vue 3 前端源码（零改动）
├── templates/vue/            # 前端构建产物（零改动）
├── backend/
│   ├── paths.py              # REPO_ROOT / APP_DIR / _config_dir / 旧配置迁移
│   ├── errring.py            # stderr 错误历史圈（werkzeug+uvicorn 双格式识别）
│   ├── gateway.py            # 网关用户身份：get_gateway_user / require_admin（Depends 依赖）
│   ├── version.py            # APP_VERSION（读 manifest）/ fnOS 版本 / GitHub 更新检查
│   ├── shell.py              # 外部命令层：run_cmd/sudo_cmd（shell=False）+ 工具路径常量 + log
│   ├── jsonstore.py          # JSON 配置读写
│   ├── caching.py            # @ttl_cache 装饰器
│   ├── state.py              # 跨模块共享状态单点归属（_TEMP_SNAP / _CPU_USAGE_CACHE / FAN_LOCK / FAN_TARGETS）
│   ├── collectors/           # 采集层（12 模块，互相独立）
│   │   ├── cputemp.py        #   sensors 解析 + 统一温度快照循环
│   │   ├── cpuusage.py       #   真实 CPU 使用率（1s 固定窗口）
│   │   ├── raid.py           #   阵列卡（storcli 解析器群）
│   │   ├── smart.py          #   SMART 解析（SAS/ATA/NVMe）
│   │   ├── disks.py          #   硬盘枚举/温度/自检 A-B-C
│   │   ├── board.py          #   主板/内存（dmidecode/decode-dimms）
│   │   ├── system.py         #   系统/RAPL 功耗/网卡
│   │   ├── storage.py        #   存储卷
│   │   ├── dockermod.py      #   Docker
│   │   ├── ports.py          #   端口占用
│   │   ├── gpu.py            #   GPU 实时（核显/NV/AMD）
│   │   ├── metrics.py        #   实时指标读数（net/disk io/rapl）
│   │   └── wol.py            #   网络唤醒
│   ├── fans/                 # 风扇域（7 模块）
│   │   ├── hwmon.py          #   sysfs 读写（pwm/enable）
│   │   ├── enumerate.py      #   风扇枚举（pwm*_enable 全量扫描）
│   │   ├── modes.py          #   模式持久化 + 启动恢复
│   │   ├── labels.py         #   命名/别名
│   │   ├── fcs.py            #   FanControlServer 接管互斥
│   │   ├── rules.py          #   温控曲线 + 逐扇联动规则
│   │   └── control.py        #   平滑 PWM 循环 + get_fan_status
│   ├── history/db.py         # 历史趋势（SQLAlchemy 2.x Core，samples 表结构冻结）
│   ├── health/               # 硬盘健康分级 / 告警通知 / 健康报告+脱敏
│   ├── schedule.py           # 阵列卡一致性检查定时调度
│   ├── web/
│   │   ├── app.py            # create_app()：FastAPI 组装 + 统一异常处理器
│   │   ├── deps.py           # json_body 依赖（复刻 get_json(silent=True)）+ _jr 响应助手
│   │   ├── staticpages.py    # / 、/vue、/ui/images、/manual（SSE→StreamingResponse）
│   │   └── routers/          # 8 组路由器：basic / all / raid / disks / res / metrics / fans / alerts
│   ├── daemon.py             # 后台线程唯一启动点（6 线程 + metrics 主循环 + 预热）
│   └── serve.py              # main()：uvicorn 三模式（网关 UDS / TCP 侧车 / 本地 TCP）
├── scripts/
│   ├── extract_routes.py     # 路由奇偶校验（--check 与基线 JSON diff，必须为空）
│   ├── routes_baseline.json  # 旧 Flask 版 58 条 path-method 基线
│   ├── test_app_parsers.py   # 59 个回归测试（pytest）
│   └── migration/            # 一次性拆分工具留档（split_app / fix_imports / build_routers）
└── docs/                     # 使用手册 + 重构计划 + 开发计划 + 本文档
```

## 二、与 Flask 版的差异点

| # | 差异 | Flask 版 | FastAPI 版 | 兼容性影响 |
|---|---|---|---|---|
| 1 | 框架/服务器 | Flask + werkzeug dev server | FastAPI 0.100+ + uvicorn（ASGI） | 无（端口/监听方式不变） |
| 2 | 钩子→依赖注入 | `@require_admin()` 装饰器 | `Depends(require_admin)` | 无（403 体逐字节一致 `{"ok":false,"error":...}`） |
| 3 | 请求体 | `request.get_json(force=True, silent=True)` | `deps.json_body` 异步依赖预解析 | 无（同步端点经 anyio 线程池，语义复刻；非法 body 均静默为 None） |
| 4 | Query 参数 | `request.args.get(...)` | `request.query_params.get(...)` | 无 |
| 5 | 响应 | `jsonify(x), code` | `_jr(x, code)`（JSONResponse 透传） | 无（出参不做模型过滤，逐字节兼容） |
| 6 | SSE | `Response(stream_with_context(gen()))` | `StreamingResponse` | 无（chunked + X-Accel-Buffering: no 保留） |
| 7 | 静态文件 | `send_from_directory`（自带防穿越） | `FileResponse` + 自实现 `_send_under()` 钳制 | 无（越界路径仍 404） |
| 8 | 网关 Unix Socket | wsgiref + AF_UNIX + chmod 0o777 | uvicorn `uds=` + startup 钩子 chmod 0o777 | 无（cmd/main 零改动） |
| 9 | 前缀中间件 | WSGI `_PrefixMiddleware`（改 environ） | ASGI 版（改 scope） | 无（GATEWAY_PREFIX 行为一致） |
| 10 | 历史存储 | sqlite3 直驱 | SQLAlchemy 2.x Core | 无（samples 表结构冻结，旧 history.db 直接续用，缺列自动补） |
| 11 | 新增 | — | `/docs`、`/openapi.json`（OpenAPI 自动文档） | 纯增量；经网关时同样受飞牛登录态保护 |
| 12 | 新增 | 500 返回 werkzeug HTML 错误页 | 统一 JSON：`{"ok":false,"error":...}`（含 422 结构校验） | 前端对 4xx/5xx 均走通用错误分支，不受影响 |
| 13 | 认证 | 网关 Header（X-Trim-*） | 相同，**未引入 JWT** | 无（网关已完成登录态校验，保持信任链） |
| 14 | 线程模型 | 6 条线程在 import 期启动 | 同样 6 条，由 `serve.main()` 启动序列显式拉起（顺序不变） | 部署无感；`import backend.*` 不再有副作用（可安全测试） |

### 顺带修复的原代码缺陷（行为增强，均记录在代码注释）

1. `_json.load`（fcs.py 区）未定义即用 → 有 `/boot/board.json` 的机型 `_fcs_has_board_config` 恒 False；改 `json.load`
2. `_debug(...)` ×4（raid/storage/diskhealth/res）未定义即用 → 异常路径静默失效或 NameError；改 `shell.log(...)`
3. `metrics_collect_loop` 用裸名 `threading.Thread`，靠文件末尾 import 侥幸存活 → 模块自带 import
4. `_parse_ver` 同名双定义（导入期/运行期算法不同）→ 拆名 `_parse_ver_simple` / `_parse_ver`，语义各自保留

## 三、需要修改/关注的配置点

| 配置 | 位置 | 说明 |
|---|---|---|
| `TRIM_PKGVAR` | 环境变量 | 飞牛注入的用户数据目录（配置 JSON + history.db）；未注入时自动探测 `/vol*/@appdata/com.dashboard.nasdash` |
| `TRIM_SERVICE_PORT` | 环境变量 | 本地 TCP 端口（manifest `service_port=9800` 注入）；不设则默认 9800 |
| `NAS_DASH_GATEWAY=1` | 环境变量 | 启用网关 Unix Socket 模式（由 cmd/main 注入） |
| `APP_SOCKET` / `GATEWAY_PREFIX` | 环境变量 | socket 路径与网关前缀（默认 `APP_DIR/app.sock`、`/app/com.dashboard.nasdash`） |
| 依赖安装 | `cmd/install_callback` | 自动 `pip install -r requirements.txt`（fastapi/uvicorn/sqlalchemy，含 Pydantic v2） |
| 打包 | `build.sh` L17 | tar 白名单已含 `backend requirements.txt`，并排除 `__pycache__` |

## 四、验证结论（2026-09-27）

| 验收项 | 结果 |
|---|---|
| 路由奇偶校验（基线 58 条 path-method vs FastAPI openapi） | **58/58 完全一致** |
| 回归测试 `scripts/test_app_parsers.py` | **59/59 通过** |
| TestClient 功能冒烟（version/me/errors 空 body/all/index/manual/403 体/404） | **8/8 通过** |
| `compileall app.py backend/` | 通过 |
| `build.sh` 产出 app.tgz 含 backend/（54 个 py，无 __pycache__）+ verify.sh 三处 md5 一致 | 通过 |
| backend 无 flask 残留 / 无 `from app import` / `__file__` 仅锚定 paths | 通过 |
| 真实起服务联验（local_smoke.sh） | 待飞牛环境执行（本机为生产 NAS，真实启动会接管风扇 PWM，避免在测试中触碰硬件；TestClient 已覆盖 ASGI 栈全链路） |
