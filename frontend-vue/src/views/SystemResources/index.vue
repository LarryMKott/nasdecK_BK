<script setup lang="ts">
/**
 * 阶段 2 · 系统资源页（Vue 版，正式入口第一页）
 *
 * 复刻 templates/index.html 的 renderSystem + fetchNetRates 三段式刷新：
 *  - /api/system  首屏 + 每 30s（含 60s 服务端缓存）：内存明细、运行时长、显卡清单
 *  - /api/metrics 每 1s（纯读缓存，实测 0.002~0.08s）：CPU/内存/网络/显卡/磁盘 I/O
 * 数据与老页面同源、同口径：OVS 桥去重、核显不回退显存占用、GPU 双线（温度橙 + 显存蓝）。
 * 架构迁移批2：业务逻辑下沉 composables/business/useSystemResources，
 * 接口走 api/modules/{system,metrics}。
 */
import Sparkline from '@/components/ui/Sparkline.vue'
import PanelHero from '@/components/ui/PanelHero.vue'
import { useSystemResources } from '@/composables/business/useSystemResources'
import { fmtSpeed } from '@/utils/format'

const {
  sys,
  selectedGpu,
  lastUpdate,
  busy,
  cpuPct,
  memPct,
  memInfo,
  gpuList,
  gpuName,
  gpuNameFull,
  gpuLegend,
  diskRows,
  heroStats,
  loadText,
  cpuLines,
  memLines,
  netLines,
  gpuLines,
  rxTxt,
  txTxt,
  refreshAll,
} = useSystemResources()
</script>

<template>
  <div>
    <PanelHero
      icon="system"
      title="系统资源"
      sub="CPU / 内存 / 网络实时状态"
      :stats="heroStats"
      :last-update="lastUpdate"
      :busy="busy"
      @refresh="refreshAll"
    />

    <div class="sys-grid">
      <div class="card sys-area-cpu">
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 6px; justify-content: space-between">
          <h3 style="margin: 0" title="CPU 实时使用率（飞牛系统资源页风格）">CPU</h3>
          <span :style="{ fontSize: '30px', fontWeight: 700, color: 'var(--blue)', lineHeight: 1 }">
            {{ cpuPct != null ? cpuPct + '%' : '—' }}
          </span>
        </div>
        <div style="position: relative">
          <Sparkline :lines="cpuLines" :max="100" :w="600" :h="100" :css-h="80" :line-width="2" />
        </div>
      </div>

      <div class="card sys-area-mem">
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 6px; justify-content: space-between">
          <h3 style="margin: 0" title="内存实时使用率（飞牛系统资源页风格）">内存</h3>
          <span :style="{ fontSize: '30px', fontWeight: 700, color: '#a855f7', lineHeight: 1 }">
            {{ memPct != null ? memPct + '%' : '—' }}
          </span>
        </div>
        <div style="position: relative">
          <Sparkline :lines="memLines" :max="100" :w="600" :h="100" :css-h="80" :line-width="2" />
        </div>
        <div style="margin-top: 8px; font-size: 12px">
          <div class="kv"><span class="k">应用占用</span><span class="v" style="font-weight: 600">{{ memInfo.used || '—' }}</span></div>
          <div class="kv">
            <span
              class="k"
              title="可用 = 缓存 + 真空闲。缓存是系统把读过的文件留在内存里备用，程序需要时会自动腾出来，不用手动清理"
            >
              可用（含缓存 {{ memInfo.cached || '—' }}，可自动回收）
            </span>
            <span class="v" style="font-weight: 600">{{ memInfo.available || '—' }}</span>
          </div>
        </div>
      </div>

      <div class="card sys-area-disk">
        <h3 title="磁盘的读写速度，数字越大说明盘越忙">磁盘 I/O</h3>
        <template v-if="diskRows.length">
          <div v-for="(d, i) in diskRows" :key="i" class="kv">
            <span class="k">
              {{ d.device }}<span v-if="d.standby" class="tag-sleep">待机</span>
              <template v-if="[d.brand, d.model, d.size].filter(Boolean).join(' · ')">
                <br /><small style="color: var(--muted)">{{ [d.brand, d.model, d.size].filter(Boolean).join(' · ') }}</small>
              </template>
            </span>
            <span class="v" style="font-size: 12px">
              读{{ fmtSpeed(d.read_rate) }} / 写{{ fmtSpeed(d.write_rate) }}
              <span v-if="typeof d.busy === 'number'" class="disk-busy">占用 {{ d.busy }}%</span>
            </span>
          </div>
        </template>
        <div v-else class="loading">采样中…</div>
      </div>

      <div class="card sys-area-netlive">
        <div style="display: flex; justify-content: space-between; align-items: baseline; margin: 0 0 6px; gap: 8px">
          <h3 style="margin: 0; color: var(--text-1)" title="实时上下行速率（合并全部网口）">网络</h3>
          <div class="gpu-legend">
            <span class="gpu-legend-item">
              <i style="width: 10px; height: 3px; border-radius: 2px; display: inline-block; background: var(--blue)" />↑ 上传
            </span>
            <span class="gpu-legend-item">
              <i style="width: 10px; height: 3px; border-radius: 2px; display: inline-block; background: var(--green)" />↓ 下载
            </span>
          </div>
        </div>
        <div style="position: relative">
          <Sparkline :lines="netLines" max="auto" :w="800" :h="120" :css-h="80" />
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 13px; font-weight: 600; margin-top: 4px">
          <span style="color: var(--blue)">↑ {{ txTxt }}</span>
          <span style="color: var(--green)">↓ {{ rxTxt }}</span>
        </div>
      </div>

      <div class="card sys-area-gpu">
        <div class="gpu-head-row">
          <h3>显卡</h3>
          <select v-if="gpuLegend" class="gpu-select" :value="selectedGpu" @change="selectedGpu = Number(($event.target as HTMLSelectElement).value)">
            <option v-for="(g, i) in gpuList" :key="i" :value="i">
              {{ g.name || g.type || '显卡' }}{{ g.type ? ' · ' + g.type : '' }}
            </option>
          </select>
        </div>
        <div v-if="gpuList.length" class="gpu-block">
          <div class="gpu-name" :title="gpuNameFull">{{ gpuName }}</div>
          <div class="gpu-legend">
            <span class="gpu-legend-item"><i class="gpu-dot temp" />温度</span>
            <span class="gpu-legend-item"><i class="gpu-dot mem" />显存</span>
          </div>
          <div class="gpu-combo"><Sparkline :lines="gpuLines" :max="100" :w="600" :h="90" /></div>
        </div>
        <div v-else class="loading">未检测到核显 / 独显，或 BIOS 已禁用</div>
      </div>
    </div>

    <div style="margin-top: 12px; font-size: 12px; color: var(--text-2)">
      负载 {{ loadText }}（共 {{ sys?.cpu_threads ?? '—' }} 线程）
    </div>
  </div>
</template>
