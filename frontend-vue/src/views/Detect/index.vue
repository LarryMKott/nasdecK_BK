<script setup lang="ts">
/**
 * 阶段 3 · 硬件配置检测（Vue 原生页）
 *
 * 复刻 templates/index.html 的 renderDetect（老页 line 3756-4172）：
 *  - 快照：/api/all（system + raid + disks + time）—— 检测页是整机概览，与老页同走全量
 *  - 实时：/api/metrics 每 1s（CPU 全核平均频率，蓝色 .js-cpu-freq-live）
 *          /api/fan/temps 每 5s（CPU 温度 #sys-cpu-temp-kv）
 * 交互（网口 Tab / 阵列卡定位·一致性检查·热备·CopyBack / 定时巡检）沿用老页同名全局函数挂到 window，
 * 因此 v-html 内的 inline onclick 与老页逐字一致；这些函数全部走 apiFetch（带 index.cgi 前缀，子路径下也通）。
 *
 * 【架构迁移批2续】命令式脚本整体下沉 composables/business/useDetect（机制逐字保留：
 * v-html + window 函数挂载 + DOM id 操作）；api 调用细化为域模块函数列入下批。
 */
import PanelHero from '@/components/ui/PanelHero.vue'
import { useDetect } from '@/composables/business/useDetect'

const { err, busy, lastUpdate, bodyEl, bodyHtml, heroStats, badge, sub, refreshAll } = useDetect()
</script>

<template>
  <div>
    <PanelHero
      icon="detect"
      title="硬件配置检测"
      :sub="sub"
      :badge="badge"
      :stats="heroStats"
      :last-update="lastUpdate"
      :busy="busy"
      @refresh="refreshAll"
    />
    <div v-if="err" class="loading" style="color: var(--red)">{{ err }}</div>
    <div v-show="!err" ref="bodyEl" v-html="bodyHtml" />
    <div class="modal-overlay" id="memDetailModal" onclick="if(event.target===this)closeMemDetail()">
      <div class="modal-box mem-modal">
        <div class="modal-title" id="memDetailTitle">内存详情</div>
        <div class="modal-detail" id="memDetailBody"></div>
        <div class="modal-actions"><button class="btn" onclick="closeMemDetail()">关闭</button></div>
      </div>
    </div>
  </div>
</template>
