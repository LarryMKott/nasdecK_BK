<script setup lang="ts">
/**
 * 关于 nasdeck（Vue 版，阶段 3 第 2 个迁移模块）
 *
 * 复刻 templates/index.html 的 renderAbout：
 *  - PanelHero 副标题显示当前版本（nasdeck {current}）
 *  - 版本信息卡：fnOS 版本（/api/system 顶层 fnos_version）、运行内核（/api/system system.kernel）
 *  - 项目卡：项目主页 / 论坛地址外链 + 「检查新版本」按钮（拉 /api/version?force=1）
 *
 * 样式全部复用全局 panel.css 的 .detect-cards/.card/.kv/.btn-mini/.card h3（与旧页同源），
 * 本组件只补 .about-err/.about-update 两个局部状态条，不重定义布局类，避免 scoped 的 data-v 失配。
 * 架构迁移批2：业务逻辑下沉 composables/business/useAbout，接口走 api/modules/system。
 */
import PanelHero from '@/components/ui/PanelHero.vue'
import { useAbout } from '@/composables/business/useAbout'

const {
  appVersion,
  fnosVersion,
  kernel,
  error,
  lastUpdate,
  checking,
  updateMsg,
  updateUrl,
  updateFound,
  checkUpdate,
} = useAbout()
</script>

<template>
  <div>
    <PanelHero
      icon="about"
      title="关于 nasdeck"
      :sub="appVersion ? 'nasdeck ' + appVersion : 'nasdeck'"
      :last-update="lastUpdate"
    />

    <div v-if="error" class="about-err">{{ error }}</div>

    <div class="detect-cards" v-show="!error">
      <div class="card">
        <h3>版本信息</h3>
        <div class="kv">
          <span class="k" title="飞牛系统版本，相当于手机底层的操作系统">fnOS 版本</span>
          <span class="v">{{ fnosVersion ? fnosVersion + ' (Debian 底层)' : '（切到「硬件配置检测」后自动显示）' }}</span>
        </div>
        <div class="kv">
          <span class="k" title="系统内核版本，最底层的那层程序">运行内核</span>
          <span class="v">{{ kernel || '—' }}</span>
        </div>
      </div>

      <div class="card">
        <h3>项目</h3>
        <div class="kv">
          <span class="k">项目主页</span>
          <span class="v"><a href="https://github.com/LarryMKott/nasdeck" target="_blank" rel="noopener">github.com/LarryMKott/nasdeck</a></span>
        </div>
        <div class="kv">
          <span class="k">论坛地址</span>
          <span class="v"><a href="https://club.fnnas.com/forum.php?mod=viewthread&tid=67060" target="_blank" rel="noopener">club.fnnas.com 讨论帖</a></span>
        </div>
        <div class="kv">
          <span class="k">检查更新</span>
          <span class="v"><button class="btn-mini" type="button" :disabled="checking" @click="checkUpdate">{{ checking ? '⏳' : '检查新版本' }}</button></span>
        </div>
      </div>
    </div>

    <div v-if="updateMsg" class="about-update" :class="{ found: updateFound }">
      <span>{{ updateMsg }}</span>
      <a v-if="updateFound" :href="updateUrl" target="_blank" rel="noopener">前往下载 ↗</a>
    </div>
  </div>
</template>

<style scoped>
.about-err {
  color: var(--red, #e5484d);
  padding: 12px 14px;
  font-size: 13px;
}
.about-update {
  margin-top: 14px;
  padding: 10px 14px;
  border-radius: var(--r-md, 10px);
  background: var(--card);
  border: 1px solid var(--border);
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.about-update.found {
  border-color: var(--green, #2da44e);
}
.about-update a {
  color: var(--link);
  text-decoration: none;
}
.about-update a:hover {
  text-decoration: underline;
}
</style>
