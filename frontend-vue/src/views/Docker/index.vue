<script setup lang="ts">
/**
 * 阶段 3 · Docker 页（Vue 版）
 *
 * 复刻 templates/index.html 的 renderDocker：
 *  - /api/docker 取容器列表（状态/镜像/CPU/内存/网络速率与累计/端口/运行时长）
 *  - 旧页每 8s 轮询一次，这里同样 8s 定时刷新（内存/CPU% 由后端已按逻辑核数归一化，前端直接显示）
 *  - 网络列：有实时速率显示 ↑↓ 速率，无速率显示累计收发（与旧页口径一致）
 * 架构迁移批2：业务逻辑下沉 composables/business/useDocker，接口走 api/modules/docker。
 */
import PanelHero from '@/components/ui/PanelHero.vue'
import { useDocker } from '@/composables/business/useDocker'

const {
  containers,
  heroStats,
  lastUpdate,
  busy,
  error,
  refreshAll,
  fmtCpu,
  fmtMem,
  netDown,
  netUp,
  netTitle,
} = useDocker()
</script>

<template>
  <div>
    <PanelHero
      icon="docker"
      title="Docker"
      sub="容器运行状态与资源占用"
      :stats="heroStats"
      :last-update="lastUpdate"
      :busy="busy"
      @refresh="refreshAll"
    />

    <div class="card">
      <h3>说明</h3>
      <div class="note">
        列出所有容器及其运行状态、占用内存与端口映射。内存仅对运行中容器有效；停止的容器显示 N/A。
      </div>
    </div>

    <div v-if="error" class="card" style="border-color: var(--danger)">
      <div class="note" style="color: var(--danger)">{{ error }}</div>
    </div>

    <div class="section-title" style="margin-top: 14px">容器列表</div>
    <div class="card">
      <table class="table">
        <thead>
          <tr>
            <th>状态</th>
            <th>名称 / 镜像</th>
            <th>CPU</th>
            <th>内存</th>
            <th>网络速率 (↓收/↑发)</th>
            <th>端口映射</th>
            <th>运行时长</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(c, i) in containers" :key="i">
            <td>
              <span class="badge" :class="c.running ? 'b-ok' : 'b-warn'">
                {{ c.running ? '运行' : '停止' }}
              </span>
            </td>
            <td>
              <b>{{ c.name }}</b
              ><br /><span style="font-size: 12px; color: var(--muted)">{{ c.image }}</span>
            </td>
            <td style="white-space: nowrap; text-align: right">{{ fmtCpu(c) }}</td>
            <td style="white-space: nowrap">
              {{ fmtMem(c)
              }}<template v-if="c.mem_pct != null"
                ><br /><small style="color: var(--muted)">{{ c.mem_pct.toFixed(1) }}%</small></template
              >
            </td>
            <td
              style="font-size: 12px; white-space: nowrap"
              :title="netTitle(c)"
            >
              ↓{{ netDown(c) }}<br />↑{{ netUp(c) }}
            </td>
            <td style="font-size: 12px">{{ c.ports || '-' }}</td>
            <td style="font-size: 12px; color: var(--muted)">{{ c.runtime || '—' }}</td>
          </tr>
          <tr v-if="!containers.length">
            <td colspan="7" class="loading">未检测到 Docker / 无容器</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
