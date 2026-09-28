<script setup lang="ts">
/**
 * 阶段 3 · 存储卷页（Vue 版）
 *
 * 复刻 templates/index.html 的 renderStorage(st)（旧页 3608-3651）：
 *  - /api/storage 取 {raid_arrays, volumes, topology, cloud_mounts}
 *  - hero 四指标：RAID 阵列数 / 存储卷数 / 平均使用率(仅算本地卷) / 拓扑有无
 *  - 三段：RAID 阵列表 + 存储卷容量表(进度条「智能两态」+ 云盘标记) + 存储拓扑 <pre>
 *  - **纯只读**：此页无任何写操作（RAID 卡控制属于「硬件配置检测」页）。
 *    旧页对存储卷默认也不轮询（auto-refresh 默认关），这里同样只在挂载时拉一次 + 手动刷新。
 * 架构迁移批2：业务逻辑下沉 composables/business/useStorage，接口走 api/modules/storage。
 */
import PanelHero from '@/components/ui/PanelHero.vue'
import { useStorage } from '@/composables/business/useStorage'

const {
  raidArrays,
  volumes,
  topology,
  vmap,
  heroStats,
  lastUpdate,
  busy,
  error,
  refreshAll,
  raidOk,
  cloudLabel,
  pctNum,
  insideFill,
  fillStyle,
  outStyle,
  shortModel,
  focusDisk,
} = useStorage()
</script>

<template>
  <div>
    <PanelHero
      icon="storage"
      title="存储卷"
      sub="RAID 阵列 / 卷容量 / 拓扑"
      :stats="heroStats"
      :last-update="lastUpdate"
      :busy="busy"
      @refresh="refreshAll"
    />

    <div v-if="error" class="card" style="border-color: var(--danger)">
      <div class="note" style="color: var(--danger)">{{ error }}</div>
    </div>

    <!-- 卷映射（v2.3.0 3.6）：结构对齐飞牛原生「存储空间管理」，盘块可点跳健康卡 -->
    <div class="section-title">卷映射</div>
    <div class="card">
      <!-- 空态：无卷也无独立盘时给一行占位（避免渲染成空白卡片） -->
      <div v-if="!(vmap?.volumes?.length) && !(vmap?.loose?.length)" class="note" style="text-align:center;padding:12px 0 8px">
        暂无卷映射信息（未检测到存储卷或独立盘）
      </div>
      <div v-for="v in vmap?.volumes || []" :key="v.mount" class="vmap-vol">
        <div class="vmap-head">
          <b class="vmap-name">{{ v.mount }}</b>
          <span class="vmap-cap">容量 {{ v.used || '—' }} / {{ v.size || '—' }}</span>
        </div>
        <div class="vmap-bar"><i :style="{ width: v.pcent || '0%' }" /></div>
        <div class="vmap-meta">
          <span v-if="v.md"><i>RAID</i>{{ v.md.name }} · {{ v.md.level }} · {{ v.md.state }}</span>
          <span v-else><i>RAID</i>无（直连）</span>
          <span v-if="v.lvm" :title="'LVM 卷名：' + v.lvm"><i>逻辑卷</i>LVM</span>
          <span><i>文件系统</i>{{ v.fs }}</span>
        </div>
        <div class="vmap-cap2">使用硬盘 {{ v.disks.length }}</div>
        <div class="vmap-pds">
          <button
            v-for="d in v.disks"
            :key="d.dev"
            class="vmap-pd"
            :title="(d.model || '') + '（点击跳到硬盘 SMART 看健康详情）'"
            @click="focusDisk(d.dev)"
          >
            <b>{{ d.dev }}</b>
            <span class="vmap-sub">{{ shortModel(d.model) }}</span>
            <span v-if="d.slot" class="vmap-sub">槽位 {{ d.slot }}</span>
            <span class="vmap-temp" :class="{ asleep: d.asleep }">{{ d.asleep ? '休眠' : d.temp != null ? d.temp + '℃' : '—' }}</span>
            <span class="vmap-go">›</span>
          </button>
        </div>
      </div>
      <div v-if="vmap?.loose?.length" class="vmap-vol" style="border-bottom:0;padding-bottom:2px">
        <div class="vmap-cap2">未入卷的盘 · {{ vmap.loose.length }} 块（独立盘，不属于任何存储卷）</div>
        <div class="vmap-pds">
          <button
            v-for="d in vmap.loose"
            :key="d.dev"
            class="vmap-pd"
            :title="(d.model || '') + '（点击跳到硬盘 SMART 看健康详情）'"
            @click="focusDisk(d.dev)"
          >
            <b>{{ d.dev }}</b>
            <span class="vmap-sub">{{ shortModel(d.model) }}</span>
            <span class="vmap-temp" :class="{ asleep: d.asleep }">{{ d.asleep ? '休眠' : d.temp != null ? d.temp + '℃' : '—' }}</span>
            <span class="vmap-go">›</span>
          </button>
        </div>
      </div>
    </div>

    <div class="section-title">RAID 阵列</div>
    <div class="card">
      <table class="table">
        <thead>
          <tr>
            <th>阵列</th>
            <th>级别</th>
            <th>状态</th>
            <th>容量</th>
            <th>成员盘</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(a, i) in raidArrays" :key="i">
            <td>{{ a.name }}</td>
            <td>
              <span class="badge" :class="raidOk(a) ? 'b-ok' : 'b-bad'">{{ a.level }}</span>
            </td>
            <td>{{ a.state }}</td>
            <td>{{ a.size }}</td>
            <td>{{ (a.disks || []).join(', ') }}</td>
          </tr>
          <tr v-if="!raidArrays.length">
            <td colspan="5">无</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="section-title">存储卷容量</div>
    <div class="card">
      <table class="table">
        <thead>
          <tr>
            <th>挂载点</th>
            <th>文件系统</th>
            <th>总容量</th>
            <th>已用/可用</th>
            <th>使用率</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="(v, i) in volumes" :key="i">
            <!-- 云挂载（飞牛「远程挂载」的网盘）：容量由云端掌握、后端不报配额 -->
            <tr v-if="v.cloud">
              <td>
                {{ v.mount }}
                <div class="cloud-tag">☁ {{ cloudLabel(v) }}</div>
              </td>
              <td>{{ v.cloud_proto || 'rclone WebDAV' }}</td>
              <td style="color: var(--c-text-2)">—</td>
              <td style="color: var(--c-text-2)">—</td>
              <td style="color: var(--c-text-2)">云端挂载 · 不提供容量</td>
            </tr>
            <tr v-else>
              <td>{{ v.mount }}</td>
              <td>{{ v.fstype }}</td>
              <td>{{ v.size }}</td>
              <td>{{ v.used }} / {{ v.avail }}</td>
              <td class="pct-cell">
                <div class="progress">
                  <div class="progress-fill" :style="fillStyle(v)">
                    {{ insideFill(v) ? pctNum(v) + '%' : '' }}
                  </div>
                  <span v-if="!insideFill(v)" class="progress-pct" :style="outStyle(v)">
                    {{ pctNum(v) }}%
                  </span>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="!volumes.length">
            <td colspan="5">无</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="section-title">存储拓扑</div>
    <div class="card"><pre>{{ topology }}</pre></div>
  </div>
</template>
