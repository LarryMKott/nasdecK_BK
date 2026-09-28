# NasDeck 后端 - 标准大型项目（整洁架构）重构方案

> 项目：NasDeck（飞牛OS fnOS NAS硬件监控面板，FPK应用包）
> 现状：Flask单文件迁移至FastAPI，Vue3单文件前端；已有成熟硬件采集、风扇控制、SQLite历史时序库；项目v2.3.3，开源。
> 架构目标：采用**整洁架构(Clean Architecture)** 标准大型项目分层，领域与基础设施强隔离；存量采集逻辑复用，不推翻成熟采集代码；适配fnOS FPK轻量化打包约束。
> 执行顺序与验收门禁见 [`docs/整洁架构重构开发计划.md`](整洁架构重构开发计划.md)（方案回答"改成什么样"，计划回答"按什么顺序改、怎么验收、怎么回滚"）。

## 一、核心架构原则

1. **依赖向内**：内层（领域层）不依赖外层（Web、数据库、系统命令）；外层适配器依赖内层抽象契约。
2. **领域独立**：领域层仅使用Python标准库，不导入FastAPI、SQLAlchemy、subprocess等外部包。
3. **职责单一**：每层只做本层职责，禁止跨层混杂业务逻辑。
4. **增量迁移**：保留现有`collectors/fans/health/history`核心采集能力，逐步改造，随时可打包发布FPK。
5. **测试优先**：领域、应用层可脱离硬件、数据库、Web进行单元测试。

### 分层流向（由内向外）

`Domain(领域层)` → `Application(应用/用例层)` → `Infrastructure(基础设施适配器)` / `Presentation(表现层Web)`

> 依赖方向：外层依赖内层，内层绝不反向依赖外层。

## 二、完整目录结构

```
nasdeck/
├── backend/
│   ├── __init__.py
│   ├── main.py                      # 应用启动入口、DI容器组装、生命周期事件
│   ├── domain/                      # 【领域层｜最内层，纯业务，无外部依赖】
│   │   ├── __init__.py
│   │   ├── entities/                # 领域实体（业务对象，dataclass）
│   │   │   ├── hardware.py
│   │   │   ├── fan.py
│   │   │   ├── disk.py
│   │   │   ├── temperature.py
│   │   │   └── health.py
│   │   ├── interfaces/              # 抽象接口契约（ABC）
│   │   │   ├── collector_repo.py
│   │   │   ├── history_repo.py
│   │   │   ├── fan_driver.py
│   │   │   └── alert_repo.py
│   │   └── rules/                   # 纯领域业务规则（无IO、无外部调用）
│   │       ├── fan_rule.py
│   │       └── health_judge.py
│   ├── application/                 # 【应用层｜UseCase业务用例编排】
│   │   ├── __init__.py
│   │   ├── usecases/
│   │   │   ├── dashboard_usecase.py
│   │   │   ├── fan_control_usecase.py
│   │   │   ├── disk_health_usecase.py
│   │   │   ├── history_query_usecase.py
│   │   │   └── automation_usecase.py
│   │   └── dto/                     # 应用层DTO，领域与用例之间数据传输
│   │       └── __init__.py
│   ├── infrastructure/              # 【基础设施层｜实现领域抽象接口】
│   │   ├── __init__.py
│   │   ├── collectors/              # 原有硬件采集模块迁移至此
│   │   │   ├── base.py
│   │   │   ├── cpu_collector.py
│   │   │   ├── raid_collector.py
│   │   │   ├── smart_collector.py
│   │   │   └── ...
│   │   ├── fans/                    # 风扇硬件驱动实现（原有fans模块）
│   │   │   ├── hwmon_driver.py
│   │   │   └── fan_manager.py
│   │   ├── persistence/             # SQLite持久化、SQLAlchemy仓储实现
│   │   │   ├── db.py
│   │   │   ├── history_repo_impl.py
│   │   │   └── alert_repo_impl.py
│   │   ├── command/                 # 系统命令执行底层：storcli / smartctl / sensors
│   │   │   └── cmd_runner.py
│   │   ├── config/
│   │   │   └── settings.py
│   │   └── logging/
│   │       └── logger.py
│   ├── presentation/                # 【表现层｜FastAPI Web接口】
│   │   ├── __init__.py
│   │   ├── api/
│   │   │   ├── v1/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── endpoints/       # 按前端页面模块拆分路由
│   │   │   │   │   ├── dashboard.py
│   │   │   │   │   ├── temperature.py
│   │   │   │   │   ├── fan_control.py
│   │   │   │   │   ├── smart.py
│   │   │   │   │   ├── history.py
│   │   │   │   │   └── ...
│   │   │   │   ├── router.py        # 路由聚合入口
│   │   │   │   └── dependencies.py  # fnOS登录鉴权、请求上下文依赖
│   │   └── schemas/                 # Pydantic模型：HTTP请求/响应序列化
│   │       ├── dashboard_schema.py
│   │       ├── fan_schema.py
│   │       └── common_schema.py
│   ├── tasks/                       # 后台异步任务：定时采集、时序降采样
│   │   ├── __init__.py
│   │   └── collector_task.py
│   └── common/                      # 全局公共组件
│       ├── __init__.py
│       ├── exceptions.py            # 业务异常定义
│       └── helpers.py
├── frontend-vue/                    # Vue3前端工程【保持不变】
├── templates/
│   └── vue/
│       └── index.html               # vite-plugin-singlefile打包产物
├── tests/
│   ├── unit/
│   │   ├── test_domain/             # 领域规则单元测试，无IO
│   │   └── test_application/        # 用例测试，mock基础设施
│   ├── integration/
│   │   └── test_infrastructure/     # 采集器、数据库、命令集成测试
│   └── api/
│       └── test_presentation/       # HTTP接口集成测试
├── docs/                            # 项目开发文档
├── build.sh                         # FPK打包脚本，保留原有双模式
├── manifest.json                    # fnOS FPK清单文件
├── pyproject.toml
└── README.md
```

## 三、各层职责边界

### 1. domain 领域层（最内层）

- 职责：定义业务实体、领域规则、抽象接口契约。
- 可导入：仅Python标准库。
- ❌ 禁止导入：FastAPI、SQLAlchemy、subprocess、基础设施代码。
- 示例：磁盘健康判定规则、风扇调速业务规则、硬件指标实体。
- 价值：纯业务逻辑，**脱离硬件、数据库、Web即可单元测试**。

### 2. application 应用层（UseCase用例层）

- 职责：编排业务流程；调用领域实体 + 领域抽象接口。
- 不感知：数据来源、存储介质、HTTP请求。
- 入/出参：使用应用层DTO，**不使用Web层Pydantic Schema**。
- 依赖：仅导入`domain`。

### 3. infrastructure 基础设施层

- 职责：**实现domain定义的抽象接口**；对接操作系统、硬件、SQLite、shell命令。
- 原有`collectors/fans/history`模块迁移至此。
- 依赖：导入domain抽象接口，实现接口契约。

### 4. presentation 表现层（FastAPI Web）

- 职责：HTTP请求解析、Pydantic参数校验、fnOS鉴权、调用UseCase、组装HTTP响应、静态页面挂载。
- ❌ 禁止：业务逻辑、直接调用采集器、直接操作数据库。
- 依赖：仅导入`application`用例与`common`异常。

### 5. tasks 后台任务

独立于Web服务，asyncio后台任务，定时采集、时序数据降采样；**复用application层UseCase**，Web接口与后台采集共用同一套业务规则。

### 6. common

全局异常、基础工具函数，跨层可用，无业务逻辑。

## 四、依赖注入（DI）设计

> 在`main.py`统一组装实例，构造函数注入；单元测试可直接Mock基础设施适配器，无需真实硬件。

```python
# backend/main.py
from fastapi import FastAPI
from domain.interfaces.history_repo import IHistoryRepo
from infrastructure.persistence.history_repo_impl import HistoryRepoImpl
from application.usecases.dashboard_usecase import DashboardUseCase
from presentation.api.v1.router import api_router

# 1.实例化基础设施实现
history_repo: IHistoryRepo = HistoryRepoImpl()

# 2.注入到应用用例
dashboard_usecase = DashboardUseCase(history_repo=history_repo)

# 3.将usecase注册到FastAPI依赖容器，供路由Depends注入
app = FastAPI(title="NasDeck fnOS硬件监控", version="2.3.3")
app.include_router(api_router, prefix="/api/v1")

@app.on_event("startup")
async def startup():
    # 初始化数据库、启动后台采集任务
    pass

@app.on_event("shutdown")
async def shutdown():
    # 释放资源，关闭后台任务、数据库连接
    pass

if __name__ == "__main__":
    import uvicorn
    from infrastructure.config.settings import settings
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
```

## 五、分层代码示例

### 1. domain/entities/disk.py（领域实体）

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class DiskHealth:
    disk_id: str
    temperature: int
    power_on_hours: int
    is_healthy: bool
```

### 2. domain/interfaces/collector_repo.py（抽象契约）

```python
from abc import ABC, abstractmethod
from domain.entities.disk import DiskHealth

class IHardwareCollectorRepo(ABC):
    @abstractmethod
    async def get_disk_health(self) -> list[DiskHealth]:
        ...
```

### 3. application/usecases/disk_health_usecase.py（应用用例）

```python
from domain.interfaces.collector_repo import IHardwareCollectorRepo

class DiskHealthUseCase:
    def __init__(self, collector_repo: IHardwareCollectorRepo):
        self.collector_repo = collector_repo

    async def execute(self):
        # 编排业务流程
        disk_list = await self.collector_repo.get_disk_health()
        return disk_list
```

### 4. infrastructure/collectors/smart_collector.py（基础设施实现）

```python
from domain.interfaces.collector_repo import IHardwareCollectorRepo
from domain.entities.disk import DiskHealth
from infrastructure.command.cmd_runner import run_cmd

class SmartCollectorRepo(IHardwareCollectorRepo):
    async def get_disk_health(self) -> list[DiskHealth]:
        raw_output = await run_cmd(["smartctl", "-a", "/dev/sda"])
        # 解析命令输出，映射为领域实体
        return [DiskHealth(
            disk_id="sda",
            temperature=42,
            power_on_hours=1200,
            is_healthy=True
        )]
```

### 5. presentation/api/v1/endpoints/disk.py（路由层）

```python
from fastapi import APIRouter, Depends
from application.usecases.disk_health_usecase import DiskHealthUseCase
from presentation.schemas.disk_schema import DiskHealthResp

router = APIRouter(tags=["硬盘健康"])

@router.get("/disk/health", response_model=list[DiskHealthResp])
async def get_disk_health(
    usecase: DiskHealthUseCase = Depends()
):
    result = await usecase.execute()
    return result
```

## 六、测试体系（大型项目标准分层测试）

```
tests/
├── unit/
│   ├── test_domain/        # 领域规则单元测试，无IO，不需要硬件
│   └── test_application/   # 用例测试，mock基础设施抽象接口
├── integration/
│   └── test_infrastructure/# 采集器、SQLite、系统命令集成测试
└── api/
    └── test_presentation/  # HTTP接口集成测试，启动完整应用
```

1. Domain单元测试：纯业务规则校验，不依赖fnOS与硬件。
2. Application层测试：Mock仓储抽象接口，验证业务编排逻辑。
3. Integration测试：真实调用`smartctl/storcli`，验证采集适配器。
4. API测试：端到端接口回归。

## 七、fnOS / FPK平台适配约束

1. 网络：服务固定监听`127.0.0.1:9800`，通过`index.cgi + curl`反代访问；鉴权逻辑放置在presentation层依赖。
2. 任务：后台采集使用原生`asyncio`任务，**不引入Celery/Redis**，保持FPK轻量化。
3. 打包：`build.sh`保留双模式构建；打包时剔除测试、开发文档，仅保留运行时代码与使用手册。
4. 权限：所有API接口强制fnOS账号登录校验，未登录拦截。

## 八、增量迁移实施步骤

> 低风险，可随时中断、打包发布FPK版本

1. 新建`domain`与`application`目录，定义领域实体、抽象接口契约。
2. 将原有采集代码封装为`infrastructure`适配器，实现domain抽象接口。
3. 按10个前端面板，逐个编写UseCase。
4. 改造presentation路由，路由仅调用UseCase，移除路由内聚合业务代码。
5. 分层迁移pytest用例，优先编写domain单元测试。
6. 改造后台定时采集任务，复用UseCase。
7. 全接口回归测试，FPK打包验证，fnOS环境验证cgi反代与权限。
8. 清理旧冗余代码，完成架构标准化。

## 九、架构收益

1. **业务与底层完全解耦**：更换采集命令、切换存储引擎，domain/application无需改动。
2. **单元测试能力大幅提升**：业务规则可脱离硬件、操作系统独立验证。
3. **依赖方向受控**：内层永远不依赖外层，多人并行开发互不污染。
4. **业务收敛**：所有业务规则收敛在domain，不会散落在路由/采集器各处。
5. **可替换适配器**：开发环境可使用Mock采集器，不需要真实NAS硬件。

## 十、可选后续优化项

1. 采集结果内存缓存，减少高频重复调用`smartctl/storcli`。
2. 增加请求链路Trace日志，定位慢采集命令。
3. 接口限流，防止前端频繁刷新触发大量系统命令。
4. 采集器降级策略：storcli执行失败自动降级到lspci读取阵列信息。
