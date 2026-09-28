<script setup lang="ts">
/**
 * 阶段 3 · 端口占用页（Vue 版，v2.3.0 新增）
 *
 *  - /api/ports 取宿主机所有监听端口（TCP/UDP）+ 占用进程 + 来源（docker:容器名 / 宿主机）
 *  - 每 10s 轮询刷新（端口变化不频繁，10s 足够）
 *  - 「释放」按钮：二次确认弹窗 → POST /api/ports/release 向占用进程发 SIGTERM
 *    后端黑名单禁止杀系统关键进程（nasdeck 自身 / PID1 / systemd / dockerd / sshd 等）
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import PanelHero from '@/components/ui/PanelHero.vue'
import { ICON_PNG } from '@/assets/icons'
import { usePorts, STATE_HINT } from '@/composables/business/usePorts'
import type { ScopeFilter } from '@/composables/business/usePorts'

const {
  uniquePorts,
  keyword,
  kw,
  scopeFilter,
  FILTERS,
  DIST,
  scopeCounts,
  pickFilter: pickScope,
  filteredPorts,
  searching,
  isSub,
  portGroup,
  groupOrder,
  heroStats,
  lastUpdate,
  busy,
  error,
  releasing,
  msg,
  msgType,
  confirmOpen,
  target,
  scopeInfo,
  reachInfo,
  stateText,
  isKernel,
  iconFailed,
  appIconUrl,
  onIconError,
  askRelease,
  doRelease,
  closeConfirm,
  refreshAll,
} = usePorts()

/* ---------- 来源下拉（自定义，避免原生 select 在 8s 轮询下闪动 + 统一深浅主题）。
   键盘导航/外点收起是纯交互态，留在视图层；筛选数据与动作在 usePorts。 ---------- */
const ddOpen = ref(false)
const ddBox = ref<HTMLElement | null>(null)
const ddActive = ref(0) // 键盘导航高亮项
const curFilterLabel = computed(() => {
  const f = FILTERS.find((x) => x.id === scopeFilter.value) || FILTERS[0]
  return f.label + '（' + scopeCounts.value[f.id] + '）'
})
function toggleDd() {
  ddOpen.value = !ddOpen.value
  if (ddOpen.value) ddActive.value = FILTERS.findIndex((f) => f.id === scopeFilter.value)
}
/** 选中筛选项并收起（usePorts.pickFilter 只改数据，这里顺带收起下拉）。 */
function pickFilter(id: ScopeFilter) {
  pickScope(id)
  ddOpen.value = false
}

/** 键盘：↑↓ 移动、Enter 选中、Esc 关闭。 */
function ddKey(e: KeyboardEvent) {
  if (!ddOpen.value) {
    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      toggleDd()
    }
    return
  }
  if (e.key === 'Escape') {
    ddOpen.value = false
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    ddActive.value = (ddActive.value + 1) % FILTERS.length
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    ddActive.value = (ddActive.value - 1 + FILTERS.length) % FILTERS.length
  } else if (e.key === 'Enter') {
    e.preventDefault()
    pickFilter(FILTERS[ddActive.value].id)
  }
}
/** 点页面其他地方收起下拉。 */
function onDocMouseDown(e: MouseEvent) {
  if (ddOpen.value && ddBox.value && !ddBox.value.contains(e.target as Node)) ddOpen.value = false
}
/** Esc 收起下拉（挂在 document 上，不依赖焦点是否还在按钮上）。 */
function onDocKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && ddOpen.value) ddOpen.value = false
}

onMounted(() => {
  document.addEventListener('mousedown', onDocMouseDown)
  document.addEventListener('keydown', onDocKey)
})
onUnmounted(() => {
  document.removeEventListener('mousedown', onDocMouseDown)
  document.removeEventListener('keydown', onDocKey)
})
</script>

<template>
  <div>
    <PanelHero
      icon="ports"
      title="端口占用"
      sub="宿主机监听端口与占用进程"
      :stats="heroStats"
      :last-update="lastUpdate"
      :busy="busy"
      @refresh="refreshAll"
    />

    <div class="card">
      <h3>说明</h3>
      <div class="note">
        列出 NAS 上所有正在监听的 TCP/UDP 端口、占用进程与来源。<b>来源分三类</b>：
        <b>应用</b>＝飞牛应用中心安装的应用（自动识别，带该应用自己的图标）；
        <b>容器</b>＝Docker 容器映射出来的端口；
        <b>系统</b>＝飞牛系统自带服务，并标注中文说明（如「飞牛文件共享」「飞牛 Web 管理后台」）。
        <br /><b>应用归属怎么来的</b>：nasdeck 在 NAS 本机读取进程信息——进程的运行用户、可执行文件路径、
        工作目录与命令行，逐级比对应用安装目录（<code>/vol1/@appcenter/&lt;应用&gt;</code>），
        并沿父进程向上追溯，因此「应用用 python/node 拉起的子进程」也能归到对应应用名下，无需手工配置。
        <br />同一端口可能被<b>多个进程</b>同时占用（UDP 允许多进程复用同一端口；Docker 也会为 IPv4/IPv6 各起一个代理进程）。
        这类行会<b>全部列出</b>、以「↳ 同一端口」标记分组，不做合并，避免漏掉占用者。
        <br />地址后面的徽章表示<b>可达范围</b>（按「绑了哪张网卡」判定，不是看防火墙）：
        <b>对外</b>＝局域网里任何机器都能连（绑所有网卡、物理网卡 IP、网段广播、组播）；
        <b>容器网</b>＝只有本机与 Docker 容器网络能连，局域网连不上；
        <b>仅本机</b>＝只在 127.0.0.1 / ::1 上，出了这台 NAS 谁都连不上。
        <br />「状态」列：<b>监听中</b>＝TCP 端口已开好、等人来连；<b>未连接</b>＝UDP 端口已绑好、可以收包
        （UDP 不建立连接，两者含义其实相同）。
        少数行的进程显示为「<b>内核</b>」，那是内核线程持有的端口（如 NFS，<code>ss</code> 读不到进程名），
        已按端口号反查补出来源说明；这类端口没有可终止的用户进程，故不能释放。
        释放端口会向占用进程发送终止信号（SIGTERM）——请确认该进程可以关闭，避免误杀系统服务。
      </div>
    </div>

    <div v-if="msg" class="card" :style="{ borderColor: msgType === 'ok' ? 'var(--success)' : 'var(--danger)' }">
      <div class="note" :style="{ color: msgType === 'ok' ? 'var(--success)' : 'var(--danger)' }">{{ msg }}</div>
    </div>

    <div v-if="error" class="card" style="border-color: var(--danger)">
      <div class="note" style="color: var(--danger)">{{ error }}</div>
    </div>

    <div class="section-title port-toolbar" style="margin-top: 14px">
      <span class="port-head">
        监听端口
        <span class="port-dist">
          <span v-for="d in DIST" :key="d.id" class="dist-item" :class="d.cls">
            {{ d.label }}<b>{{ scopeCounts[d.id] }}</b>
          </span>
        </span>
        <span v-if="searching" class="port-sub">
          匹配 {{ filteredPorts.length }} / {{ uniquePorts.length }} 条
        </span>
      </span>
      <div class="port-tools">
        <div ref="ddBox" class="port-dd" @keydown="ddKey">
          <button
            class="dd-btn"
            :class="{ open: ddOpen }"
            :aria-expanded="ddOpen"
            aria-haspopup="listbox"
            title="按来源筛选：应用 / 容器 / 系统"
            @click="toggleDd"
          >
            <span class="dd-cur">{{ curFilterLabel }}</span>
            <i class="dd-caret" aria-hidden="true"></i>
          </button>
          <div v-if="ddOpen" class="dd-menu" role="listbox" aria-label="端口来源">
            <div
              v-for="(f, i) in FILTERS"
              :key="f.id"
              class="dd-item"
              :class="['k-' + f.id, { on: scopeFilter === f.id, hl: ddActive === i }]"
              role="option"
              tabindex="-1"
              :aria-selected="scopeFilter === f.id"
              @click="pickFilter(f.id)"
              @mouseenter="ddActive = i"
            >
              <i class="dd-dot" aria-hidden="true"></i>
              <span class="dd-label">{{ f.label }}</span>
              <span class="dd-n">{{ scopeCounts[f.id] }}</span>
            </div>
          </div>
        </div>
        <input
          v-model="keyword"
          class="port-search"
          type="search"
          placeholder="搜索端口 / 进程 / 应用 / 来源…"
          aria-label="搜索监听端口"
        />
      </div>
    </div>
    <div class="card">
      <!-- port-table：固定布局 + 显式列宽（除「来源」列吃剩余空间）。不用固定布局时，
           表格 auto 布局会把余量优先喂给「刚性」列 —— 地址列因为带 nowrap 的徽章/组标记
           而刚性最大，实测白占 338px（内容只 131px），「来源」列被挤到中文名逐字换行。
           列宽改动的取舍与实测数据见 panel.css 里 .port-table 的注释。 -->
      <table class="table port-table">
        <thead>
          <tr>
            <th class="c-proto">协议</th>
            <th class="c-addr">本地地址:端口</th>
            <th class="c-state">状态</th>
            <th class="c-proc">进程</th>
            <th class="c-pid">PID</th>
            <th class="c-src">来源</th>
            <th class="c-act">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(p, i) in filteredPorts" :key="p.proto + '|' + p.local + '|' + p.port + '|' + (p.pid ?? -1)">
            <td><span class="badge" :class="p.proto === 'tcp' ? 'b-ok' : 'b-info'">{{ p.proto.toUpperCase() }}</span></td>
            <!-- 本格内容**只允许「地址 + 可达徽章」两件**，且外面套 flex（.port-addr-cell）：
                 格内一旦并排第三件（如「共 N 个进程」），三者都不可压缩，该列的刚性最小宽度
                 就会超过我们设定的 224px，窄窗口下整行被迫变高（用户两次截图反馈）。
                 同端口有多进程这件事，改由**续行**标记 `↳ 同一端口（2/3）` 承担 —— 首行因此必然单行。 -->
            <td class="port-addr-cell">
              <template v-if="isSub(p, i)">
                <span
                  class="port-sub"
                  :title="'该端口共 ' + portGroup(p) + ' 个进程在监听（UDP 允许多进程复用同一端口；Docker 也会为 IPv4/IPv6 各起一个代理进程）'"
                >↳ 同一端口（{{ groupOrder(p, i) }}/{{ portGroup(p) }}）</span>
              </template>
              <template v-else>
                <span class="port-addr" :title="p.local + ':' + p.port">{{ p.local }}:{{ p.port }}</span>
                <span
                  v-if="reachInfo(p)"
                  class="badge"
                  :class="reachInfo(p)?.cls"
                  style="margin-left: 4px"
                  :title="reachInfo(p)?.hint"
                >{{ reachInfo(p)?.text }}</span>
              </template>
            </td>
            <td style="font-size: 12px; color: var(--muted); white-space: nowrap" :title="STATE_HINT[p.state] || ''">{{ stateText(p) }}</td>
            <td style="font-size: 13px">
              <template v-if="p.process">
                <span class="port-proc" :title="p.process">{{ p.process }}</span>
              </template>
              <span
                v-else-if="isKernel(p)"
                class="port-kernel"
                :title="'内核态服务：端口由内核线程持有（如 NFS），读不到进程名' + (p.desc ? '——' + p.desc : '')"
              >内核</span>
              <span v-else style="color: var(--muted)">—</span>
            </td>
            <td class="port-num" style="font-size: 13px">{{ p.pid != null ? p.pid : '—' }}</td>
            <td>
              <!-- 来源格用 flex 行：徽章/图标占自然宽，应用名 flex:1 吃剩余并省略。
                   用 inline-block + max-width 时，名称**不会**收缩到可用空间，只会整块掉到
                   第二行 —— 实测窄窗（849px）下来源格内容区 159px、内容需 160px，差 1px
                   就让 25 行由 47px 涨到 65px。flex + min-width:0 后永不变高，只截断。 -->
              <div class="port-src-cell">
                <span class="badge" :class="scopeInfo(p).cls">{{ scopeInfo(p).type }}</span>
                <img
                  v-if="p.icon && !iconFailed[p.icon]"
                  :src="appIconUrl(p.icon)"
                  class="app-ic"
                  :title="(p.appname || p.icon) + (p.app ? '（' + p.app + '）' : '')"
                  alt=""
                  @error="onIconError(p.icon)"
                />
                <img
                  v-else-if="p.scope.startsWith('docker')"
                  :src="ICON_PNG.docker"
                  class="app-ic"
                  title="Docker 容器"
                  alt=""
                />
                <span
                  class="port-src-name"
                  style="font-size: 12px; color: var(--muted); margin-left: 6px"
                  :title="scopeInfo(p).name + (scopeInfo(p).raw && scopeInfo(p).raw !== scopeInfo(p).name ? '（' + scopeInfo(p).raw + '）' : '')"
                >{{ scopeInfo(p).name }}</span>
              </div>
            </td>
            <td>
              <button
                class="port-rel"
                :disabled="p.pid == null || releasing"
                :title="p.pid != null
                  ? '终止占用该端口的进程（PID ' + p.pid + '）'
                  : (isKernel(p) ? '内核态服务由内核持有，无法按进程释放' : '该端口没有可终止的进程')"
                @click="askRelease(p)"
              >
                释放
              </button>
            </td>
          </tr>
          <tr v-if="!filteredPorts.length">
            <td colspan="7" class="loading">
              {{ kw
                ? '没有匹配「' + keyword.trim() + '」的端口'
                : (scopeFilter !== 'all' ? '该分类下暂无端口' : '未检测到监听端口 / 接口异常') }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 释放二次确认 -->
    <div v-if="confirmOpen && target" class="modal-overlay show" @click.self="closeConfirm">
      <div class="modal-box">
        <div class="modal-title">确认释放端口</div>
        <div class="modal-body">此操作会终止占用该端口的进程，端口随即释放。请确认该进程可以关闭。</div>
        <div v-if="portGroup(target) > 1" class="modal-body" style="color: var(--warning)">
          注意：该端口还有另外 {{ portGroup(target) - 1 }} 个进程在监听，终止本进程后端口<b>仍会被这些进程占用</b>。
        </div>
        <div class="modal-detail">
          <p class="kv">端口：{{ target.local }}:{{ target.port }}（{{ target.proto.toUpperCase() }}）</p>
          <p class="kv">进程：{{ target.process }}（PID {{ target.pid }}）</p>
          <p class="kv">
            来源：{{ scopeInfo(target).type }}{{ scopeInfo(target).name ? ' · ' + scopeInfo(target).name : '' }}
            <img
              v-if="target.icon && !iconFailed[target.icon]"
              :src="appIconUrl(target.icon)"
              class="app-ic"
              alt=""
              @error="onIconError(target.icon)"
            />
            <img
              v-else-if="target.scope.startsWith('docker')"
              :src="ICON_PNG.docker"
              class="app-ic"
              alt=""
            />
          </p>
        </div>
        <div class="modal-actions">
          <button class="btn" @click="closeConfirm">取消</button>
          <button class="btn btn-danger" :disabled="releasing" @click="doRelease">
            {{ releasing ? '释放中…' : '确认释放' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
