<script setup lang="ts">
/**
 * 风扇控制 —— 复刻旧页 renderFanControl 及其全部交互
 * （旧页 templates/index.html：2245-3470 主体 + 3473-3605 曲线编辑器）。
 *
 * 为什么这页用 v-html + 同名全局函数（而不是结构化 Vue 模板）：
 *   这是一个「大子系统」——风扇卡片 / 曲线编辑器 / 3 套模式体 / 硬盘休眠联动 / 温度概览，
 *   全靠 DOM id 就地操作的命令式代码（滑块 120ms 防抖、1s 轮询就地把 textContent 改掉、
 *   canvas 手动重绘、classList 切换「接管锁定」态）。逐字移植保真度最高、行为与旧页完全一致。
 *   交互函数沿用旧页同名并挂到 window（v-html 里的 inline onclick 才有效），卸载时注销。
 *
 * hero 用 PanelHero（Vue 组件），「接管风扇控制」开关走 #action 插槽；
 * 页面主体走 v-html，整体重渲染靠 :key 强制换 DOM —— 等价旧页 `innerHTML = …`，
 * 保证轮询就地改过的 textContent 一定被重置（否则会出现「旧值残留」）。
 *
 * 数据源：GET /api/system?force=1（后端注释写明「供 #system 与 #fan 按需刷新」）。
 *
 * 【架构迁移批2续】命令式脚本整体下沉 composables/business/useFan（机制逐字保留）；
 * api 调用细化为域模块函数列入下批。
 */
import PanelHero from '@/components/ui/PanelHero.vue'
import { useFan } from '@/composables/business/useFan'

const {
  pageHtml,
  renderSeq,
  ctrlEnabled,
  ctrlChanging,
  heroStats,
  lastUpdate,
  busy,
  loadError,
  loadFanData,
  onToggleCtrl,
} = useFan()
</script>

<template>
  <div id="fan">
    <PanelHero
      icon="fan"
      title="风扇温控"
      sub="预设档位 / 实时转速 / 曲线调速"
      :stats="heroStats"
      :last-update="lastUpdate"
      :busy="busy"
      :auto-tick="false"
      @refresh="loadFanData(true)"
    >
      <template #action>
        <label class="hero-switch" title="关闭后 nasdeck 不再根据温度自动调速。若飞牛自带风扇服务已配置好，会交还它接管；否则保持当前转速，不再写 PWM。">
          <input id="fanCtrlToggle" type="checkbox" :checked="ctrlEnabled" :disabled="ctrlChanging" @change="onToggleCtrl" />
          <span class="hero-switch-track"><span class="hero-switch-knob" /></span>
          <span class="hero-switch-txt">接管风扇控制</span>
        </label>
      </template>
    </PanelHero>

    <div v-if="loadError" class="man-loaderr">{{ loadError }}</div>
    <div :key="renderSeq" v-html="pageHtml" />
  </div>
</template>
