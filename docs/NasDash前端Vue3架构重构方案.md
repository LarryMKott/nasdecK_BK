# NasDash 前端 Vue3 大型项目架构重构方案

> **状态（2026-09-28）：批1+批2 已实施** —— 目录切层 / vue-router / pinia / api 层 / 9/12 页 composable 下沉 / 令牌抽离 / vitest 46 用例全过，产物 1873KB ≤2.2MB。
> 实施记录、决策差异（axios 不引入·静态 import·v-html 三页顺延）与下批候选见 **[`docs/前端架构迁移说明.md`](前端架构迁移说明.md)**；本方案保留为目标架构蓝本。
>
> 项目：NasDash（飞牛OS fnOS NAS硬件监控面板，FPK应用包）
> 现状（2026-09-29 更新）：前端为 Vue3 + Vite，~~产出单文件 `index.html`（`vite-plugin-singlefile`）~~ 已拆为多文件产物 `index.html + assets/*`（见前端架构迁移说明.md 第四节），用于 fnOS FPK 应用；当前页面共10个业务面板；玻璃拟态UI、设计令牌、响应式。
> 架构目标：**企业级大型前端标准架构**，适配现有单文件打包产物约束；和后端整洁架构对齐，前后端职责清晰；支持多人协作、单元测试、主题/明暗模式、移动端适配；保留最终构建产物为独立 `index.html` 的特性。

## 一、核心设计原则

1. **和后端整洁架构对齐**：前端只做展示、表单交互、状态编排；**业务规则放后端**，前端不实现硬件判断、健康告警核心逻辑。
2. **分层单向数据流**：`API层 → 状态层 → 业务组件 → UI原子组件`，禁止跨层直接调用。
3. **产物约束不变**：构建后输出**单个 index.html**，适配飞牛OS cgi反代，规避静态资源404问题；产物体积控制 ≤2.2MB。
4. **职责隔离**：
    - 纯UI组件：只接收props、触发事件，不请求接口、不包含业务逻辑；
    - 业务组件：组装UI组件，使用状态；
    - API：统一封装请求，集中处理错误、鉴权。
5. **可测试**：组件、状态逻辑可单元测试；业务逻辑抽离为可独立测试的composable。
6. **主题统一**：使用Design Token管理色彩、间距、圆角，统一明暗两套主题。

## 二、目录结构（标准大型Vue3项目，`frontend-vue/`）

```
frontend-vue/
├── index.html                 # vite入口模板
├── vite.config.js             # 打包配置，保留 vite-plugin-singlefile
├── package.json
├── tsconfig.json              # 推荐TypeScript（大型项目强类型约束）
├── .eslintrc.js
├── .prettierrc
├── src/
│   ├── main.ts                # 应用入口，创建app、注册全局插件、路由
│   ├── app.vue                # 根组件
│   ├── assets/                # 静态资源（图标、字体，尽量svg）
│   │   ├── icons/
│   │   └── styles/
│   │       ├── reset.css
│   │       └── tokens/        # Design Token 设计令牌（light/dark）
│   ├── router/                # 路由层
│   │   ├── index.ts
│   │   └── routes/            # 按业务模块拆分路由定义
│   │       ├── dashboard.ts
│   │       ├── temperature.ts
│   │       ├── smart.ts
│   │       └── ...
│   ├── stores/                # 全局状态 Pinia（大型项目标准）
│   │   ├── index.ts           # store导出入口
│   │   ├── app.store.ts       # 全局：主题、侧边栏、加载状态
│   │   ├── dashboard.store.ts
│   │   ├── temperature.store.ts
│   │   ├── disk.store.ts
│   │   ├── fan.store.ts
│   │   └── history.store.ts
│   ├── api/                   # 网络请求层【独立，和UI完全解耦】
│   │   ├── client.ts          # axios实例、拦截器、全局错误处理、fnOS鉴权token
│   │   ├── request.ts         # 请求封装
│   │   └── modules/           # 按后端API v1模块拆分接口
│   │       ├── dashboard.api.ts
│   │       ├── temperature.api.ts
│   │       ├── smart.api.ts
│   │       ├── fan.api.ts
│   │       └── history.api.ts
│   ├── composables/           # 组合式业务逻辑（可复用逻辑，大型项目核心）
│   │   ├── common/            # 通用非业务hooks
│   │   │   ├── useTheme.ts
│   │   │   ├── useLoading.ts
│   │   │   └── useAsync.ts
│   │   └── business/          # 业务hooks，页面复用逻辑
│   │       ├── useDashboardData.ts
│   │       ├── useDiskHealth.ts
│   │       ├── useFanControl.ts
│   │       └── useHistoryChart.ts
│   ├── components/            # 组件分层：原子组件 → 业务组件
│   │   ├── ui/                # 基础原子UI组件（无业务，纯展示）
│   │   │   ├── Card/
│   │   │   ├── Chart/
│   │   │   ├── Badge/
│   │   │   ├── Table/
│   │   │   └── Sidebar/
│   │   └── business/          # 业务组件（绑定业务数据，可跨页面复用）
│   │       ├── HardwareInfoCard/
│   │       ├── DiskSmartItem/
│   │       ├── FanControlPanel/
│   │       └── HistoryChartBlock/
│   ├── views/                 # 页面视图（路由页面，轻量组装层）
│   │   ├── Dashboard/
│   │   │   └── index.vue
│   │   ├── SystemResource/
│   │   │   └── index.vue
│   │   ├── Temperature/
│   │   │   └── index.vue
│   │   ├── SmartDisk/
│   │   │   └── index.vue
│   │   ├── StorageVolume/
│   │   ├── FanControl/
│   │   ├── Docker/
│   │   ├── PortOccupancy/
│   │   ├── Automation/
│   │   └── About/
│   ├── types/                 # TS类型定义，和后端Pydantic Schema一一对应
│   │   ├── api/
│   │   │   ├── dashboard.types.ts
│   │   │   ├── disk.types.ts
│   │   │   └── fan.types.ts
│   │   └── common.types.ts
│   └── utils/                 # 纯工具函数，无状态、无业务
│       ├── format.ts          # 单位换算、时间格式化、字节转换
│       ├── chart.ts           # 图表工具
│       └── validator.ts
├── tests/
│   ├── unit/                  # 单元测试：composables、utils、ui组件
│   └── e2e/                   # 端到端页面冒烟测试（对应原有.smoke截图自检）
└── vite-plugin-singlefile配置维持不变
```

## 三、分层职责规范（强制约束）

### 1. `api` 网络层
- 职责：创建请求实例，统一拦截器（401鉴权、错误提示、请求超时）；按后端模块封装接口函数。
- 只返回原始后端JSON数据；**不做页面渲染、不做业务计算**。
- 类型：使用`src/types`里TS类型，和后端OpenAPI文档对齐。
> 后端`/api/v1/dashboard` → `api/modules/dashboard.api.ts`

### 2. `stores` Pinia 全局状态层
- 职责：跨页面共享数据、全局缓存、加载状态、错误状态。
- 页面私有状态优先放组件内`ref`，**不要全部塞进全局store**。
- 只调用`api`模块获取数据；store action 只编排请求，**不写复杂业务计算**（复杂业务交给后端）。

### 3. `composables` 组合式逻辑层
> Vue3大型项目核心，把页面逻辑抽离为独立hooks
- `common`：通用基础能力：主题切换、异步请求封装、loading。
- `business`：业务复用逻辑：仪表盘数据加载、风扇调速提交、历史图表数据处理。
- 可直接单元测试，**不依赖具体页面模板**。

### 4. `components` 组件层，两层拆分
1. **ui 原子组件**：纯展示组件，props驱动，无接口请求，无业务硬编码。
    例：`NsCard`、`NsBadge`、`NsLineChart`。
2. **business业务组件**：组装原子组件，接收业务实体数据，触发事件；可跨页面复用。
    例：`DiskSmartItem`，接收disk对象，渲染SMART信息，触发告警弹窗。

### 5. `views` 页面视图层（最薄）
- 职责：页面布局、引入业务组件、调用composable、绑定状态。
- ❌禁止：写大量业务逻辑、直接调用axios、复杂数据处理。
> view 只做组装，业务逻辑全部下沉到 composable / api。

### 6. `types` 类型定义
- 与后端FastAPI Pydantic Schema一一对应；可以由后端OpenAPI自动生成TS类型，减少前后端类型不一致。
> 推荐：使用`openapi-typescript`根据后端`/docs`自动生成types文件。

### 7. `utils` 工具函数
纯无状态工具：字节单位转换、时间格式化、温度数值格式化，**不访问store/api**。

## 四、单向数据流规范

```
View页面 → composable业务hook → api模块请求后端 → 返回数据 → composable处理 → 更新pinia store → 页面组件响应渲染
```
- 禁止：组件内部直接import axios发起请求
- 禁止：子组件直接修改全局store；子组件emit事件，由上层view/composable处理变更

## 五、关键代码示例

### 1. src/api/client.ts 请求基础封装

```ts
import axios from "axios";

const apiClient = axios.create({
  baseURL: "/api/v1",
  timeout: 8000,
});

// 请求拦截器：fnOS鉴权
apiClient.interceptors.request.use((config) => {
  // 注入飞牛平台鉴权信息
  return config;
});

// 响应拦截器：统一错误处理
apiClient.interceptors.response.use(
  (res) => res.data,
  (err) => {
    console.error("API请求异常", err);
    throw err;
  }
);

export default apiClient;
```

### 2. src/api/modules/dashboard.api.ts 接口模块

```ts
import apiClient from "../client";
import type { DashboardResponse } from "@/types/api/dashboard.types";

export async function getDashboardInfo(): Promise<DashboardResponse> {
  return apiClient.get("/dashboard");
}
```

### 3. src/composables/business/useDashboardData.ts 业务hook

```ts
import { ref } from "vue";
import { getDashboardInfo } from "@/api/modules/dashboard.api";
import type { DashboardResponse } from "@/types/api/dashboard.types";

export function useDashboardData() {
  const loading = ref(false);
  const data = ref<DashboardResponse | null>(null);

  const fetch = async () => {
    loading.value = true;
    try {
      data.value = await getDashboardInfo();
    } finally {
      loading.value = false;
    }
  };

  return { loading, data, fetch };
}
```

### 4. src/views/Dashboard/index.vue 页面（极薄）

```vue
<template>
  <div class="dashboard-page">
    <NsCard title="硬件概览">
      <HardwareInfoCard :info="data" />
    </NsCard>
  </div>
</template>

<script setup lang="ts">
import { useDashboardData } from "@/composables/business/useDashboardData";
const { loading, data, fetch } = useDashboardData();
fetch();
</script>
```

## 六、路由方案

使用Vue Router，路由按业务模块拆分，懒加载；

```ts
// src/router/index.ts
import { createRouter, createWebHashHistory } from "vue-router";
import dashboardRoutes from "./routes/dashboard";
import temperatureRoutes from "./routes/temperature";

const router = createRouter({
  history: createWebHashHistory(),
  routes: [...dashboardRoutes, ...temperatureRoutes],
});

export default router;
```

> 因为最终打包为单html，使用`createWebHashHistory`，规避cgi反代路径问题。

## 七、样式与主题（Design Token）

- 全部主题变量抽离到`src/assets/styles/tokens`，区分`light/dark`。
- 不允许页面内硬编码颜色、圆角。
- 全局样式重置，组件样式使用scoped。
- 保留玻璃拟态效果，统一在UI基础组件内实现。

## 八、测试体系

```
tests/
├── unit/
│   ├── utils/          # 工具函数单元测试
│   ├── composables/    # 业务hook单元测试（vitest）
│   └── components/ui/  # 基础UI组件单元测试
└── e2e/                # 端到端，页面截图冒烟（复用原.smoke自检）
```
- 使用Vitest做单元测试；Cypress/Playwright做E2E截图校验，适配明暗/移动端/桌面端。

## 九、打包约束（重要，适配fnOS FPK）

1. **保留 vite-plugin-singlefile**，最终输出**单个index.html**到`../templates/vue/index.html`。
2. 资源全部内联到html，无外部js/css文件，解决飞牛cgi反代静态404。
3. 体积护栏：构建后产物 ≤2.2MB，构建脚本增加size校验，超出则打包失败。
4. 打包脚本集成进`build.sh`，和后端一起打包FPK。

## 十、前后端协同约定

1. 后端FastAPI `/docs` OpenAPI自动生成TS类型，前端`src/types`自动同步，减少接口字段不一致。
2. 后端返回统一JSON响应结构，前端api拦截器统一处理。
3. 枚举（硬件类型、告警级别）前后端共用同一份枚举定义。
4. 所有业务判断（磁盘健康、风扇规则）放在后端，前端仅做展示与参数提交。

## 十一、增量迁移实施步骤（低风险，可随时打包发布）

1. 引入TypeScript，开启类型校验；安装Pinia、VueRouter、Vitest。
2. 搭建基础目录骨架，抽离`api`请求层，封装axios实例与拦截器。
3. 提取全局状态，新建`stores`，逐步迁移全局状态。
4. 按页面逐个重构：页面逻辑抽离为`composables`；拆分业务组件/原子UI组件。
5. 抽取Design Token，重构主题样式，消除硬编码颜色。
6. 编写单元测试：优先utils、composables。
7. 配置vite-plugin-singlefile打包，验证单html产物。
8. 全页面回归 + smoke截图自检，验证桌面/移动端、明暗主题。
9. 接入`build.sh`，打包FPK在fnOS环境验证。

## 十二、架构收益

1. **结构标准化**：大型Vue项目规范，多人协作目录清晰，模块隔离。
2. **类型安全**：TS + OpenAPI类型同步，减少接口字段BUG。
3. **逻辑复用**：composables实现跨页面业务逻辑复用，消除重复代码。
4. **可测试性**：业务逻辑抽离，hook/工具函数可独立单元测试。
5. **UI统一**：Design Token统一主题，明暗模式维护成本低。
6. **产物不变**：不破坏原有FPK单html部署方式，兼容fnOS cgi反代。

## 十三、可选优化点

1. 自动生成API类型脚本，接入openapi-typescript。
2. 组件文档：使用Vitepress预览UI组件库。
3. 虚拟滚动：磁盘列表、大容量日志列表优化渲染性能。
4. 接口请求防抖/节流，防止前端高频轮询后端采集接口。
5. 前端离线mock模式，开发环境不用启动后端服务。
