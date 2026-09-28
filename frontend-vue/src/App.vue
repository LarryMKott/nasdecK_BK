<script setup lang="ts">
/**
 * 主入口外壳（架构迁移批1起：hash 切页交由 vue-router 接管）。
 *
 * 结构（复用老页面的 .header/.container/.sidebar/.tabs/.panel 类名，
 * 因此观感与旧版面板完全一致）：
 *  - 左侧：12 个模块页签（NAV 由路由 meta 归一而来，见 router/routes）
 *  - 右侧内容区：<RouterView> 渲染当前模块页（路由切换即卸载重建，
 *    与旧 v-if 链语义一致；切回秒开由 pageCache 负责）
 *
 * v2.3.0 第 11 步「旧页退休瘦身」：旧版单页面板与 iframe 回滚宿主已全部删除，
 * 界面 100% 由本应用渲染。v2.4 架构迁移：页签数据源从手写数组改为路由 meta。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '@/components/ui/AppIcon.vue'
import { NAV } from '@/router/routes'
import { freshness } from '@/api/client'
import { useAppStore } from '@/stores/app.store'
import { useTheme } from '@/composables/common/useTheme'

const route = useRoute()
const router = useRouter()
const app = useAppStore()

// 主题（app store 单例）：壳一挂载就把 data-theme 落到 <html>，
// 不必等某个页面的 hero 控件先渲染。
useTheme()

const sidebarOpen = computed(() => app.sidebarOpen)

const ALL_IDS = NAV.flatMap(b => b.tabs.map(t => t.id))

/** 当前页签标题，供无障碍朗读 */
const currentLabel = computed(() => {
  const m = route.meta as { label?: string }
  return m.label ?? ''
})

function select(id: string): void {
  app.closeSidebar()
  // hash 路由记录当前页签：刷新后仍停在原页（不触发服务端请求）
  router.push('/' + id)
}

/** 跨页跳转总线（v2.3.0 3.6）：任意页面 dispatch nasdeck-nav 即可换页签。
 *  用 window 事件而不是 props/路由注入：页面与外壳松耦合，页面不必知道外壳结构。 */
function onNav(e: Event): void {
  const id = (e as CustomEvent).detail?.tab
  if (id && ALL_IDS.includes(id)) select(id)
}
onMounted(() => window.addEventListener('nasdeck-nav', onNav))
onUnmounted(() => window.removeEventListener('nasdeck-nav', onNav))

// 数据新鲜度（3.9，隐形版）：平时不显示；只有实时页数据超过 10 秒没刷新才弹出黄色「数据已 N 秒未更新」告警（后端卡死/轮询断开时才有用，避免常驻装饰条）
const realtime = computed(() => (route.meta as { realtime?: boolean }).realtime === true)
const now = ref(Date.now())
let tick = 0
const staleSec = computed(() => (freshness.lastAny ? Math.floor((now.value - freshness.lastAny) / 1000) : -1))
const freshnessVisible = computed(() => realtime.value && staleSec.value > 10)
onMounted(() => { tick = window.setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => { if (tick) window.clearInterval(tick) })
</script>

<template>
  <div class="header">
    <button class="btn menu-toggle" @click="app.toggleSidebar()">
      <AppIcon name="menu" /> 模块
    </button>
  </div>

  <div class="container">
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="sidebar-title">监控模块</div>
      <div class="tabs">
        <template v-for="(block, bi) in NAV" :key="bi">
          <div v-if="block.group" class="sb-group">{{ block.group }}</div>
          <button
            v-for="t in block.tabs"
            :key="t.id"
            class="tab"
            :class="{ active: t.id === route.name }"
            :title="t.label"
            @click="select(t.id)"
          >
            <AppIcon :name="t.icon" /> {{ t.label }}
          </button>
        </template>
      </div>
    </aside>

    <main class="content" :aria-label="currentLabel">
      <div v-if="freshnessVisible" class="freshness stale">
        <span class="fresh-dot" />
        数据已 {{ staleSec }} 秒未更新 · 后端可能已断开
      </div>
      <div class="panel active">
        <RouterView />
      </div>
    </main>
  </div>

  <div class="nav-overlay" :class="{ show: sidebarOpen }" @click="app.closeSidebar()" />
</template>
