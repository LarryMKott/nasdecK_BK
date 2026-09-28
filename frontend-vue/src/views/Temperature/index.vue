<script setup lang="ts">
/**
 * 温度监控页（Vue 版，阶段 1b 产出并入阶段 2 正式入口）
 *
 * 复刻 templates/index.html 的 renderTemps + 当前温度概览：
 *  - 温度墙：主板测点 + 阵列卡芯片温度 + 显卡温度，长设备名不撑破格子、文字居中
 *  - ★最准 徽标：仅 "CPU 封装温度" 标，悬停解释"CPU 内部传感器直读，最准确"
 *  - 独显/核显分支：采信后端 type（核显/独显），后端无有效 type 才回退按厂商编号判断
 *  - 每 5 秒轮询 /api/fan/temps（统一温度快照）
 */
import PanelHero from '@/components/ui/PanelHero.vue'
import { useTemperature } from '@/composables/business/useTemperature'
import { tempColor } from '@/utils/format'

const {
  lastUpdate,
  busy,
  wallEntries,
  isBest,
  statusText,
  statusOk,
  heroStats,
  overviewCats,
  fetchTemps,
} = useTemperature()
</script>

<template>
  <div>
    <PanelHero
      icon="thermo"
      title="温度监控"
      sub="主板 / CPU / 芯片组 / 阵列卡 温度与阈值"
      :stats="heroStats"
      :badge="{ ok: statusOk, text: statusText }"
      :last-update="lastUpdate"
      :busy="busy"
      @refresh="fetchTemps"
    />

    <section class="card">
      <h3>温度墙</h3>
      <div v-if="wallEntries.length" class="temp-wall">
        <div
          v-for="t in wallEntries"
          :key="t.name"
          class="tchip"
          :title="t.raw ? t.raw + '（原始测点名）' : ''"
        >
          <span class="tc-name">
            {{ t.name }}<span v-if="isBest(t.name)" class="best-tag" title="CPU 内部传感器直读，最准确">★最准</span>
          </span>
          <span class="tc-val" :style="{ color: tempColor(t.value, (t.crit || t.max || 85) as number | null) }">{{ t.value }}°C</span>
        </div>
      </div>
      <p v-else class="sub">未检测到温度测点（或数据加载中…）</p>
      <div class="note">
        主板传感器测点 + 阵列卡芯片温度 + 显卡温度全览（已过滤无效 0°C / 负温）；鼠标悬停可查看原始英文名。
        颜色：<span style="color: var(--green)">绿正常</span> ·
        <span style="color: var(--orange)">橙偏高</span> ·
        <span style="color: var(--red)">红危险</span>
      </div>
    </section>

    <section class="card temp-overview">
      <div class="ov-head">
        <span>当前温度</span>
        <span class="ov-updated">{{ lastUpdate ? '· ' + lastUpdate : '' }}</span>
      </div>
      <div v-if="overviewCats.length">
        <div v-for="cat in overviewCats" :key="cat.label" class="temp-cat">
          <span class="temp-cat-label">{{ cat.label }}</span>
          <span
            v-for="(c, i) in cat.chips"
            :key="cat.label + i"
            class="ochip"
            :class="{ off: c.dim }"
            :title="c.title"
          >
            <span v-if="c.badge" class="temp-chip-badge">{{ c.badge }}</span>
            <span class="temp-chip-name">{{ c.name }}</span>
            <span class="temp-chip-val" :style="{ color: c.color }">{{ c.val != null ? c.val + '°C' : '—' }}</span>
            <span v-if="c.tag" class="temp-chip-tag" :class="c.tagClass">{{ c.tag }}</span>
          </span>
        </div>
      </div>
      <p v-else class="sub">等待首次数据…</p>
    </section>
  </div>
</template>

<style scoped>
.sub {
  color: var(--c-text-2);
  font-size: 13px;
  margin: 0;
}
/* 温度墙（长名不撑破、居中） */
.temp-wall {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 10px;
  margin-top: 6px;
}
.tchip {
  border: 1px solid var(--c-border);
  background: var(--c-bg);
  border-radius: var(--r-md);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  white-space: normal;
  overflow-wrap: anywhere;
  align-items: center;
  text-align: center;
}
.tchip .tc-name {
  font-size: 12px;
  color: var(--c-text-2);
  width: 100%;
}
.tchip .tc-val {
  font-size: 16px;
  font-weight: 600;
  width: 100%;
}
.best-tag {
  display: inline-block;
  white-space: nowrap;
  word-break: keep-all;
  font-size: 10px;
  color: #fff;
  background: var(--c-success);
  border-radius: 4px;
  padding: 1px 5px;
  margin-left: 4px;
  vertical-align: middle;
}
.note {
  margin-top: 10px;
  font-size: 12px;
  color: var(--c-text-2);
}
/* 当前温度概览 */
.ov-head {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.ov-updated {
  font-size: 11px;
  font-weight: 400;
  color: var(--c-text-2);
  margin-left: auto;
}
.temp-cat {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: stretch;
  width: 100%;
  margin-top: 4px;
  padding-top: 6px;
  border-top: 1px dashed var(--c-border);
}
.temp-cat:first-of-type {
  margin-top: 8px;
  padding-top: 0;
  border-top: none;
}
.temp-cat-label {
  font-size: 11px;
  color: var(--c-text-2);
  min-width: 42px;
  font-weight: 600;
  align-self: center;
}
.ochip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: var(--c-bg);
  border: 1px solid var(--c-border);
  border-radius: 6px;
  padding: 5px 9px;
  font-size: 12px;
  line-height: 1.4;
  white-space: nowrap;
  min-height: 34px;
}
.ochip.off {
  opacity: 0.65;
}
.temp-chip-name {
  color: var(--c-text-2);
}
.temp-chip-badge {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 6px;
  line-height: 1.3;
  font-weight: 600;
  color: var(--c-primary);
  background: var(--c-primary-bg);
}
.temp-chip-val {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  min-width: 38px;
  text-align: right;
}
.temp-chip-tag {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 6px;
  line-height: 1.3;
}
.temp-chip-tag.off {
  color: var(--c-primary);
  background: var(--c-primary-bg);
}
.temp-chip-tag.on {
  color: var(--c-text-2);
  background: transparent;
  border: 1px solid var(--c-border);
}
</style>
