<script setup lang="ts">
/**
 * 控制与自动化（automation）—— 复刻旧页 renderAutomation / fillAutomationConfig /
 * saveAutomationConfig / testNotify / exportReport / showRunLog。
 *
 * ⚠️ 本页是阶段 3 的**第一个写操作页**，两处 POST 后端都带 @require_admin()：
 *   - 保存配置  POST /api/alerts/config
 *   - 测试通知  POST /api/alerts/test
 * 读接口：GET /api/alerts（配置 + 活动告警，告警每 15s 刷）、GET /api/report（报告 / 运行日志）。
 *
 * 与旧页的一处差异（有意为之）：旧页运行日志的分级类名写成 `lv-` + toLowerCase()，
 * 即 ERROR → `lv-error`，而 CSS 只有 `.lv-err` / `.lv-warn` —— 错误行从来没被标红过。
 * 本页改用与 CSS 对齐的 `lv-err` / `lv-warn` / `lv-info`，错误行这才真的高亮。
 * 架构迁移批2：业务逻辑下沉 composables/business/useAutomation，日志解析纯函数下沉
 * utils/logParse，接口走 api/modules/alert。
 */
import AppIcon from '@/components/ui/AppIcon.vue'
import PanelHero from '@/components/ui/PanelHero.vue'
import { useAutomation, LEVELS, CHANNELS } from '@/composables/business/useAutomation'

const {
  cfg,
  alerts,
  busy,
  lastUpdate,
  loadConfig,
  lvlHas,
  onLvlChange,
  saveState,
  saveOk,
  saveConfig,
  testNotify,
  exportState,
  redactExport,
  exportReport,
  logOpen,
  logLoading,
  logLoadErr,
  logEntries,
  logRawText,
  logFetchedAt,
  errRing,
  nE,
  nW,
  errText,
  warnText,
  lvlCls,
  statusText,
  ringOpen,
  ringRawText,
  ringConfirm,
  showRing,
  closeRing,
  clearErrRing,
  showRunLog,
  closeRunLog,
  copyText,
  toastMsg,
} = useAutomation()
</script>

<template>
  <div>
    <PanelHero
      icon="automation"
      title="控制与自动化"
      sub="温度 / 硬盘健康告警、外部通知与一键健康报告"
      :last-update="lastUpdate"
      :busy="busy"
      @refresh="loadConfig"
    />

    <!-- 活动告警 -->
    <div class="auto-card">
      <h3><AppIcon name="search" /> 活动告警 <span class="badge b-info">每 15 秒刷新</span></h3>
      <ul class="auto-alerts">
        <li v-if="!alerts.length" class="ok"><AppIcon name="check" />当前无活动告警</li>
        <li v-for="(a, i) in alerts" :key="i" :class="a.level === 'danger' ? 'danger' : 'warn'">
          <b>{{ a.title }}</b> — {{ a.detail }}
        </li>
      </ul>
    </div>

    <!-- 告警规则 -->
    <div class="auto-card">
      <h3><AppIcon name="gear" /> 告警规则</h3>
      <label class="dt-toggle"><input v-model="cfg.enabled" type="checkbox"> 启用告警监控</label>
      <div class="auto-grid" style="margin: 12px 0">
        <div class="auto-field">
          <label>CPU 最高温度</label>
          <input v-model.number="cfg.temp.cpu_max" type="number" min="40" max="100" />
          <span class="unit">°C</span>
        </div>
        <div class="auto-field">
          <label>主板/芯片组最高</label>
          <input v-model.number="cfg.temp.mb_max" type="number" min="40" max="100" />
          <span class="unit">°C</span>
        </div>
        <div class="auto-field">
          <label>硬盘最高温度</label>
          <input v-model.number="cfg.temp.disk_max" type="number" min="30" max="90" />
          <span class="unit">°C</span>
        </div>
        <div class="auto-field">
          <label>内存占用上限</label>
          <input v-model.number="cfg.memory_max" type="number" min="50" max="100" />
          <span class="unit">%</span>
        </div>
      </div>
      <label class="dt-toggle"><input v-model="cfg.temp.enabled" type="checkbox"> 启用温度告警</label>
      <label class="dt-toggle"><input v-model="cfg.disk_health" type="checkbox"> 启用硬盘健康告警（SMART pre-fail / 异常）</label>
    </div>

    <!-- 通知渠道 -->
    <div class="auto-card">
      <h3><AppIcon name="wifi" /> 通知渠道</h3>
      <label class="dt-toggle" style="margin-bottom: 12px">
        <input v-model="cfg.channels.system" type="checkbox"> 面板内提示（始终记录到应用通知日志）
      </label>

      <div class="chan">
        <h4><AppIcon name="send" /> Telegram</h4>
        <label class="dt-toggle"><input v-model="cfg.channels.telegram.enabled" type="checkbox"> 启用</label>
        <div class="chan-grid">
          <div class="auto-field">
            <label>Bot Token</label>
            <input v-model="cfg.channels.telegram.bot_token" type="text" placeholder="123456:ABCdef..." />
          </div>
          <div class="auto-field">
            <label>Chat ID</label>
            <input v-model="cfg.channels.telegram.chat_id" type="text" placeholder="123456789" />
          </div>
        </div>
      </div>

      <div class="chan">
        <h4><AppIcon name="bell" /> Bark</h4>
        <label class="dt-toggle"><input v-model="cfg.channels.bark.enabled" type="checkbox"> 启用</label>
        <div class="chan-grid">
          <div class="auto-field">
            <label>推送地址</label>
            <input v-model="cfg.channels.bark.url" type="text" placeholder="https://api.day.app/你的Key" />
          </div>
        </div>
      </div>

      <div class="chan">
        <h4><AppIcon name="mail" /> 邮件 (SMTP)</h4>
        <label class="dt-toggle"><input v-model="cfg.channels.email.enabled" type="checkbox"> 启用</label>
        <div class="chan-grid">
          <div class="auto-field"><label>SMTP 服务器</label><input v-model="cfg.channels.email.smtp_host" type="text" placeholder="smtp.qq.com" /></div>
          <div class="auto-field"><label>端口</label><input v-model.number="cfg.channels.email.smtp_port" type="number" /></div>
          <div class="auto-field"><label>账号</label><input v-model="cfg.channels.email.user" type="text" /></div>
          <div class="auto-field"><label>密码/授权码</label><input v-model="cfg.channels.email.pass" type="password" /></div>
          <div class="auto-field"><label>接收地址</label><input v-model="cfg.channels.email.to" type="text" /></div>
        </div>
      </div>

      <div class="chan" style="grid-column: 1/-1">
        <h4><AppIcon name="bell" /> 按严重级别选通知渠道</h4>
        <div class="lvl-matrix">
          <div class="lvl-row lvl-head">
            <span>级别</span>
            <span v-for="c in CHANNELS" :key="c.id">{{ c.label }}</span>
          </div>
          <div v-for="l in LEVELS" :key="l.id" class="lvl-row">
            <span>{{ l.label }}</span>
            <input
              v-for="c in CHANNELS"
              :key="c.id"
              type="checkbox"
              :checked="lvlHas(l.id, c.id)"
              @change="onLvlChange(l.id, c.id, $event)"
            />
          </div>
        </div>
        <div style="font-size: 12px; color: var(--muted); margin-top: 6px">
          严重事件默认通知所有渠道；可单独关闭某级别的某个渠道（面板日志始终记录，便于事后排查）。
        </div>
      </div>

      <div class="auto-actions">
        <button class="btn-mini" @click="saveConfig"><AppIcon name="save" /> 保存配置</button>
        <button class="btn-mini" @click="testNotify"><AppIcon name="upload" /> 发送测试通知</button>
        <span class="dt-save-state" :class="{ ok: saveOk }">
          <AppIcon v-if="saveOk" name="check" />{{ saveState }}
        </span>
      </div>
    </div>

    <!-- 一键健康报告 -->
    <div class="auto-card">
      <h3><AppIcon name="report" /> 一键健康报告</h3>
      <div style="font-size: 13px; color: var(--muted); margin-bottom: 12px">
        汇总全部硬件状态与活动告警，导出供排查留档。JSON 可直接查看，HTML 为排版报告（内含融合分级的运行日志）。<b>遇到 bug 或异常</b>时，点「查看运行日志」在弹窗中查看错误/警告（置顶着色），确认后再决定要不要复制贴给开发者。
      </div>
      <label class="dt-toggle" style="display: flex; align-items: flex-start; gap: 8px; margin-bottom: 10px">
        <input v-model="redactExport" type="checkbox" style="margin-top: 2px">
        <span>脱敏导出（把内网 IP、MAC、设备序列号、主机名、云盘账号打码，报告顶部会标「已脱敏」）——贴论坛 / 群里前建议勾上</span>
      </label>
      <div class="auto-actions">
        <button class="btn-mini" @click="exportReport('json')">导出 JSON</button>
        <button class="btn-mini" @click="exportReport('html')">导出 HTML 报告</button>
        <button class="btn-mini" @click="showRunLog"><AppIcon name="clipboard" /> 查看运行日志</button>
        <span class="dt-save-state" :class="{ ok: exportState === '已导出' }">{{ exportState }}</span>
      </div>
    </div>

    <!-- 运行日志弹窗 -->
    <div class="modal-overlay modal-wide" :class="{ show: logOpen }" @click.self="closeRunLog">
      <div class="modal-box">
        <div class="modal-title log-title-row">
          <span>运行日志（诊断）</span>
          <span v-if="logFetchedAt" class="log-fetched-at">自动更新于 {{ logFetchedAt }}（每 10 秒）</span>
        </div>
        <div class="modal-body">
          <div v-if="logLoading" class="log-empty">加载中…</div>
          <div v-else-if="logLoadErr" class="log-empty">{{ logLoadErr }}</div>
          <div v-else-if="!logEntries.length" class="log-empty">（暂无运行日志）</div>
          <template v-else>
            <div class="logstat">
              运行日志共 <b>{{ logEntries.length }}</b> 行：<b class="danger">错误 {{ nE }}</b> ·
              <b class="warn">警告 {{ nW }}</b> · 常规 {{ logEntries.length - nE - nW }}（错误/警告置顶着色，方便先看 bug）
            </div>
            <div class="log-table-wrap">
              <table class="log-table">
                <thead>
                  <tr>
                    <th class="c-ln">#</th>
                    <th class="c-ts">时间</th>
                    <th>请求 / 内容</th>
                    <th class="c-st">状态</th>
                    <th class="c-sz">大小</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(e, i) in logEntries" :key="i" :class="'lv-' + lvlCls(e.lvl)">
                    <td class="c-ln">{{ i + 1 }}</td>
                    <td class="c-ts">{{ e.ts || '—' }}</td>
                    <td class="c-req">{{ e.req || e.txt }}</td>
                    <td class="c-st">
                      <span
                        :class="e.lvl === 'ERROR' ? 'danger' : e.lvl === 'WARN' ? 'warn' : 'muted'"
                        :title="e.status ? 'HTTP ' + e.status : '程序日志行（非网页请求），没有响应大小'"
                      >{{ statusText(e) }}</span>
                    </td>
                    <!-- 程序日志行没有"响应大小"概念，此格留空（不画"—"占位），级别文字对齐在"状态"表头下 -->
                    <td class="c-sz">{{ e.size || '' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
          <div class="log-footnote">
            说明：运行日志是滚动窗口，最多保留最近 <b>60 行</b>，更早的会被新日志自动顶掉；
            真正出错的记录不受此限制——会长期保留（最近 <b>100</b> 条）。有错误记录时，下方才会出现「<b>查看错误记录</b>」按钮，没有就不显示；
            表格里标黄的「请求异常」属普通请求失败，不进错误记录。
          </div>
        </div>
        <div class="modal-actions">
          <button
            v-if="errRing.length"
            class="btn"
            title="与上方「警告」数口径不同：错误记录只收真正出错的（服务器错误 / 程序异常），长期保留最多 100 条；普通请求异常（如 404）只在上表黄色标注，不进这里"
            @click="showRing"
          >查看错误记录（{{ errRing.length }}）</button>
          <button v-if="nE > 0" class="btn" @click="copyText(errText, '复制错误日志')">复制错误日志</button>
          <button v-if="nW > 0" class="btn" @click="copyText(warnText, '复制警告日志')">复制警告日志</button>
          <button v-if="logEntries.length" class="btn btn-primary" @click="copyText(logRawText, '复制全部日志')">复制全部日志</button>
          <button class="btn" @click="closeRunLog">关闭</button>
        </div>
      </div>
    </div>

    <!-- 错误记录弹窗（二级）：后端内存长期保留，不受 60 行日志尾部限制 -->
    <div class="modal-overlay" :class="{ show: ringOpen }" @click.self="closeRing">
      <div class="modal-box ring-box">
        <div class="modal-title">错误记录（长期保留，最多 100 条）</div>
        <div class="modal-body">
          <div v-if="!errRing.length" class="log-empty">（当前没有错误记录）</div>
          <template v-else>
            <div class="logstat">
              共 <b class="danger">{{ errRing.length }}</b> 条（连续重复已合并计数）；这些记录不会被运行日志刷掉，应用重启后清零。
            </div>
            <div class="log-table-wrap ring-table-wrap">
              <table class="log-table ring-table">
                <thead>
                  <tr>
                    <th class="c-ln">#</th>
                    <th class="c-ts">捕获时间</th>
                    <th>错误内容</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(e, i) in errRing" :key="i" class="lv-err">
                    <td class="c-ln">{{ i + 1 }}</td>
                    <td class="c-ts">{{ e.ts }}</td>
                    <td class="c-txt">{{ e.text }}<span v-if="e.n > 1" class="xN">（连续出现 {{ e.n }} 次）</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </div>
        <div class="modal-actions">
          <button v-if="errRing.length" class="btn" @click="copyText(ringRawText, '复制错误记录')">复制错误记录</button>
          <button
            v-if="errRing.length"
            class="btn"
            :class="{ 'btn-danger': ringConfirm }"
            @click="clearErrRing"
          >{{ ringConfirm ? '再点一次确认清除（不可恢复）' : '清除错误记录' }}</button>
          <button class="btn" @click="closeRing">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="toastMsg" class="au-toast">{{ toastMsg }}</div>
  </div>
</template>

<style scoped>
.au-toast {
  position: fixed;
  left: 50%;
  bottom: 32px;
  transform: translateX(-50%);
  background: rgba(20, 30, 48, 0.92);
  color: #fff;
  padding: 10px 18px;
  border-radius: 10px;
  font-size: 13px;
  z-index: 9999;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25);
}
/* 日志弹窗标题行：左侧标题、右侧本次拉取时间 */
.log-title-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.log-fetched-at {
  font-size: 12px;
  font-weight: 400;
  color: var(--muted, #8a8f98);
  font-variant-numeric: tabular-nums;
}
/* 清除按钮二次确认态 */
.btn-danger {
  border-color: var(--danger, #f55050);
  color: #fff;
  background: var(--danger, #f55050);
}
/* 错误记录表格（复用 .log-table 样式，整行红色） */
/* 弹窗加宽到与日志弹窗协调（默认 420px 太窄，内容列挤成细长条） */
.ring-box {
  width: 100%;
  max-width: min(720px, calc(100vw - 32px));
}
.ring-table-wrap {
  max-height: 46vh;
}
.ring-table .c-txt {
  color: var(--danger, #f55050);
  word-break: break-all;
  white-space: pre-wrap;
}
/* 记录行垂直居中：单条短记录顶着上沿、下面拖一大块红底很难看。
   注意：下面的 .log-table tbody td 也设了 vertical-align:top 且写在后面，
   同权重时后来者赢，这里必须抬高权重（双类名）才能真正生效 */
.log-table.ring-table tbody td {
  vertical-align: middle;
  line-height: 1.6;
}
.ring-table .xN {
  margin-left: 8px;
  color: var(--muted, #8a8f98);
  font-size: 11.5px;
  white-space: nowrap;
}
/* 弹窗底部说明文字 */
.log-footnote {
  margin-top: 10px;
  font-size: 12px;
  color: var(--muted, #8a8f98);
  line-height: 1.6;
}
.log-footnote b {
  color: var(--text, #d5dae2);
  font-weight: 600;
}
/* 运行日志表格 */
.log-table-wrap {
  border: 1px solid var(--border, rgba(128, 138, 155, 0.25));
  border-radius: 10px;
  overflow: auto;
  max-height: 52vh;
}
.log-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.log-table thead th {
  position: sticky;
  top: 0;
  background: var(--card, #1a2232);
  color: var(--muted, #8a8f98);
  text-align: left;
  font-weight: 600;
  font-size: 12px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border, rgba(128, 138, 155, 0.25));
  white-space: nowrap;
  z-index: 1;
}
.log-table tbody td {
  padding: 5px 10px;
  border-bottom: 1px solid var(--border, rgba(128, 138, 155, 0.12));
  vertical-align: top;
  font-variant-numeric: tabular-nums;
}
.log-table tbody tr:last-child td {
  border-bottom: none;
}
.log-table .c-ln {
  color: var(--muted, #8a8f98);
  text-align: right;
  width: 34px;
  user-select: none;
}
.log-table .c-ts {
  white-space: nowrap;
  color: var(--muted, #8a8f98);
}
.log-table .c-req {
  word-break: break-all;
}
.log-table .c-st {
  white-space: nowrap;
  text-align: right;
}
.log-table .c-sz {
  white-space: nowrap;
  text-align: right;
  color: var(--muted, #8a8f98);
}
.log-table tr.lv-err td {
  background: var(--danger-bg, rgba(245, 80, 80, 0.12));
}
.log-table tr.lv-err .c-req {
  color: var(--danger, #f55050);
}
.log-table tr.lv-warn td {
  background: var(--warning-bg, rgba(240, 165, 30, 0.1));
}
.log-table tr.lv-warn .c-req {
  color: var(--warning, #f0a51e);
}
.log-table .muted {
  color: var(--muted, #8a8f98);
}
</style>
