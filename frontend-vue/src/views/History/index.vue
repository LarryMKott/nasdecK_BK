<script setup lang="ts">
/**
 * 历史趋势（sys-hist）—— Vue 原生页
 *
 * 复刻旧页 templates/index.html 的 initHistory / drawHist / loadHistory，
 * 以及「生成分析报告」整套（computeStats / metricNarrative / buildOverview /
 * sparkline / asciiSpark / toCSV / svgChartImg / toMarkdown / toHTML / downloadReport）。
 *
 * 数据：GET /api/history?range=24h|7d|30d —— 纯只读，无写操作、无鉴权门槛。
 * 主图为 canvas 手绘折线（6 个维度自选）；报告在浏览器本地生成后下载（不经过服务器）。
 * 架构迁移批2：数据编排与绘制下沉 composables/business/useHistory，
 * 报告纯函数下沉 utils/historyReport（可单测），接口走 api/modules/history。
 */
import PanelHero from '@/components/ui/PanelHero.vue'
import { sparkline } from '@/utils/historyReport'
import { useHistory, METRIC_TABS, RANGE_TABS, RPT_RANGES, RPT_FORMATS } from '@/composables/business/useHistory'

const {
  histMetric,
  histRange,
  busy,
  lastUpdate,
  canvasEl,
  legendHtml,
  tempThreshold,
  heroStats,
  sectionTitle,
  loadHistory,
  pickMetric,
  pickRange,
  setTempThreshold,
  fanChannels,
  reportOpen,
  rptRange,
  rptFmt,
  reportStatus,
  rptHint,
  closeReport,
  genReport,
} = useHistory()
</script>

<template>
  <PanelHero
    icon="history"
    title="历史趋势"
    sub="多维度趋势（自适应时间范围）"
    :stats="heroStats"
    :last-update="lastUpdate"
    :busy="busy"
    @refresh="loadHistory"
  />

  <div class="seg" style="margin-bottom: 8px; flex-wrap: wrap">
    <button
      v-for="m in METRIC_TABS"
      :key="m.id"
      class="seg-btn"
      :class="{ active: histMetric === m.id }"
      @click="pickMetric(m.id)"
    >
      {{ m.label }}
    </button>
  </div>

  <div style="display: flex; justify-content: flex-end; margin-bottom: 6px">
    <button class="btn btn-primary" style="font-size: 13px; padding: 6px 14px" @click="reportOpen = true">
      生成分析报告
    </button>
  </div>

  <div class="section-title">{{ sectionTitle }}</div>

  <div class="card">
    <div class="chart-head">
      <div class="seg">
        <button
          v-for="r in RANGE_TABS"
          :key="r.id"
          class="seg-btn"
          :class="{ active: histRange === r.id }"
          @click="pickRange(r.id)"
        >
          {{ r.label }}
        </button>
      </div>
      <div v-if="histMetric === 'temp'" class="thr-ctl">
        <span class="thr-label">红线</span>
        <input
          class="thr-input"
          type="number"
          min="30"
          max="120"
          step="1"
          v-model.number="tempThreshold"
          @change="setTempThreshold(tempThreshold)"
        />
        <span class="thr-unit">°C</span>
      </div>
    </div>
    <canvas ref="canvasEl" style="width: 100%; height: 170px; margin-top: 10px; display: block" />
    <div v-if="legendHtml" style="font-size: 12px; color: var(--muted); margin-top: 6px" v-html="legendHtml" />
  </div>

  <div v-if="histMetric === 'fan'" class="card" style="margin-top: 12px">
    <div class="section-title">各风扇通道历史（转速 + 接管状态）</div>
    <div style="font-size: 12px; color: var(--text-3); margin: 4px 0 10px; line-height: 1.6">
      绿点 = 软件接管(1) · 红点 = 交还主板(2)。可据此排查「哪台扇何时被谁抢走」——红点出现即说明该时刻风扇脱离了 nasdeck 控制。
    </div>
    <div v-if="fanChannels.length === 0" style="font-size: 13px; color: var(--muted)">暂无风扇通道历史数据（让 NAS 运行一段时间后会自动积累，每 30 秒记录一次）。</div>
    <div
      v-for="ch in fanChannels"
      :key="ch.name"
      style="display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--border, #eee)"
    >
      <div style="width: 96px; font-size: 13px; font-weight: 600; color: var(--text-1)">{{ ch.name }}</div>
      <div style="flex: 1; min-width: 0">
        <div v-if="ch.rpm.length >= 2" v-html="sparkline(ch.rpm, ch.color, 340, 40)"></div>
        <div v-else style="font-size: 12px; color: var(--muted)">样本不足</div>
      </div>
      <div style="width: 168px; font-size: 12px; display: flex; align-items: center; gap: 8px; white-space: nowrap">
        <span :style="{ color: ch.pe === 1 ? '#3ec97a' : ch.pe === 2 ? '#ff5d5d' : '#9aa1ad', fontWeight: 600 }">● {{ ch.pe === 1 ? '软件接管' : ch.pe === 2 ? '交还主板' : '未知' }}</span>
        <span style="color: var(--muted)">占空 {{ ch.pwm != null ? ch.pwm + '%' : '—' }}</span>
      </div>
    </div>
  </div>

  <div class="modal-overlay" :class="{ show: reportOpen }">
    <div class="modal-box">
      <div class="modal-title">生成历史趋势分析报告</div>
      <div class="modal-body">
        <div style="margin-bottom: 14px">
          <div style="font-size: 13px; color: var(--text-3); margin-bottom: 6px">时间范围</div>
          <div class="seg">
            <button
              v-for="r in RPT_RANGES"
              :key="r.id"
              class="seg-btn"
              :class="{ active: rptRange === r.id }"
              @click="rptRange = r.id"
            >
              {{ r.label }}
            </button>
          </div>
        </div>
        <div>
          <div style="font-size: 13px; color: var(--text-3); margin-bottom: 6px">导出格式（三选一）</div>
          <div class="seg">
            <button
              v-for="f in RPT_FORMATS"
              :key="f.id"
              class="seg-btn"
              :class="{ active: rptFmt === f.id }"
              @click="rptFmt = f.id"
            >
              {{ f.label }}
            </button>
          </div>
          <div style="font-size: 12px; color: var(--text-3); margin-top: 8px; line-height: 1.6" v-html="rptHint" />
        </div>
        <div style="font-size: 12px; color: var(--text-3); margin-top: 10px; min-height: 16px">{{ reportStatus }}</div>
      </div>
      <div class="modal-actions">
        <button class="btn" @click="closeReport">取消</button>
        <button class="btn btn-primary" @click="genReport(rptRange, rptFmt)">生成并下载</button>
      </div>
    </div>
  </div>
</template>
