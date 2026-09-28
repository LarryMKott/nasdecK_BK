<script setup lang="ts">
/**
 * 硬盘 SMART（disks）—— 阶段 3 第 8 页
 *
 * 复刻旧页 renderDisks / renderDiskTest / diskInfoCard 及相关自检逻辑：
 *   · 每块盘的 SMART 卡（健康徽章 / 型号 / 容量类型 / 转速 / 序列号 / 温度条
 *     + 按介质分三套细节：SAS / NVMe / ATA）
 *   · 硬盘自检（A/B/C 三档）：long / surface / badblocks
 *   · 自检进行中：进度条 / 已运行·预计剩余 / 扇区网格 / 最小化 / 中止
 *   · 自检记录弹窗（/api/disks/selftest/history）
 *
 * 写操作（POST，均 @require_admin）：/api/disks/selftest、/api/disks/selftest/abort
 *  以及磁盘卡上的「定位闪灯」→ /api/raid/locate（与硬件配置检测页同一接口）。
 *
 * 【架构迁移批2续】脚本整体下沉 composables/business/useDisks（状态/弹窗/自检轮询/
 * 展示口径全部随迁）；api 调用细化为域模块函数列入下批。
 */
import PanelHero from '@/components/ui/PanelHero.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useDisks } from '@/composables/business/useDisks'
import { tempColor } from '@/utils/format'

const {
  disks, jobs, busy, error, lastUpdate,
  minMap, stateMsg, stateErr, locateOn, locateBusy,
  pickOpen, pickDev, pickModel, pickOptions,
  confirmOpen, confirmTitle, confirmBody, confirmOk, confirmCancel, confirmDanger,
  histOpen, histDev, histLoading, histRows, histErr,
  gradeDev, gradeDisk,
  renameOpen, renameTarget, renameValue, renameBusy, renameErr,
  heroStats, badDisks, warnDisks,
  loadDisks, abortDiskTest, clearDiskTest, raidLocate,
  openPick, choosePick, closeConfirm, showDiskTestHistory,
  openGrade, openRename, saveRename,
  dataUnitShort, fmtHours, fmtSec, jobTypeName, cwBad,
  gradeLevel, gradeLabel, gradeCls, gradeColor, gradeReason, gradeExplain,
  trendUp, trendParts, counterRows, tempHot, tempTrip, fmtAge,
  diskName, diskKey, sizeText, capText, capTip, modelText, compareCells,
  elapsedOf, pctOf, etaOf, isGridJob, gridCells,
  histTypeName, histStateText, histDesc, histTime,
} = useDisks()
</script>

<template>
  <div>
    <PanelHero
      icon="hdd"
      title="硬盘 SMART"
      sub="全部硬盘健康度 / 温度 / 寿命"
      :stats="heroStats"
      :last-update="lastUpdate"
      :busy="busy"
      @refresh="loadDisks(true)"
    />

    <div v-if="error" class="card"><div class="loading">{{ error }}</div></div>

    <div v-else-if="!disks.length && !busy" class="card"><div class="loading">未检测到硬盘</div></div>

    <template v-else>
      <!-- 健康异常汇总警告 -->
      <div
        v-if="badDisks.length"
        style="background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.4);border-radius:10px;padding:14px 16px;margin-bottom:14px"
      >
        <div style="font-weight:600;color:#b91c1c;margin-bottom:6px">⚠️ 检测到 {{ badDisks.length }} 块硬盘健康异常</div>
        <ul style="margin:0 0 8px 18px;padding:0;font-size:13px;line-height:1.7">
          <li v-for="d in badDisks" :key="d.dev">
            <b>{{ diskName(d) }}</b>（{{ d.model || '未知型号' }}）：{{ gradeReason(d) }}
          </li>
        </ul>
        <div style="font-size:13px;color:var(--text)">
          建议：<b>尽快备份</b>这块盘上的重要数据，并准备更换硬盘；在换新盘之前，避免往它上面写入关键文件。
        </div>
      </div>

      <!-- 需要注意的盘（黄）：先盯住，不用马上换 -->
      <div
        v-if="warnDisks.length"
        style="background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.4);border-radius:10px;padding:14px 16px;margin-bottom:14px"
      >
        <div style="font-weight:600;color:#b45309;margin-bottom:6px">🔍 {{ warnDisks.length }} 块硬盘需要注意（先用着，但要盯住）</div>
        <ul style="margin:0 0 8px 18px;padding:0;font-size:13px;line-height:1.7">
          <li v-for="d in warnDisks" :key="d.dev">
            <b>{{ diskName(d) }}</b>（{{ d.model || '未知型号' }}）：{{ gradeReason(d) }}
          </li>
        </ul>
        <div style="font-size:13px;color:var(--text)">
          建议：把这块盘上的<span style="font-weight:600">重要数据也做一份备份</span>，并在「缺陷趋势」里留意数值有没有继续变大。
        </div>
      </div>

      <!-- 说明卡：自检会伤盘吗（默认收起，点标题展开） -->
      <details class="card disk-info-card">
        <summary><AppIcon name="bulb" /> 硬盘自检会伤盘吗？<i class="faq-hint" aria-hidden="true"></i></summary>
        <div class="note">
          <p><b class="safe">平时不伤盘</b>：本页只读 SMART 健康数据，不扫盘面、不写数据。只有手动点某块盘的「硬盘自检」才会真扫，三种里两种只读：</p>
          <p>• <b>SMART 长自检 / 只读表面扫描</b>：只读不写、不伤数据，<b>所有盘都能跑</b>（包括在用的阵列成员）。</p>
          <p>• <b>全面坏块扫描</b>：会<b class="warn">写满整盘并清空数据</b>，最彻底但很慢，只对「独立盘」开放，在用盘直接不显示这个选项。</p>
          <p><b>健康徽章</b>：<b class="safe">绿=正常</b>、<b class="warn">黄=注意</b>、<b style="color:var(--red)">红=尽快备份换盘</b>，判据全部来自盘和阵列卡自报数据。<b>点徽章</b>看详情：为什么这一档、各项计数、缺陷趋势（每 6 小时记一次，涨了徽章会挂 <b class="warn">↑N</b> 角标）。</p>
          <p><b>卡面</b>只放能横着比的：温度、缺陷/寿命、已用时长；序列号、读写量等细节都在徽章弹窗里。</p>
          <p><b>累计读写量</b>：NVMe、SATA 固态、SAS 盘都会显示；部分老款 SATA 机械盘固件不记总读写，这块硬件层面就拿不到，相应行直接不显示（不是故障）。</p>
        </div>
      </details>

      <!-- 每块盘一张卡 -->
      <div class="cards">
        <div v-for="d in disks" :key="diskKey(d)" class="card disk-card" :data-dev="diskKey(d)" :class="d.asleep ? '' : (gradeLevel(d) === 'red' ? 'bad' : '')">
          <div class="disk-head">
            <div style="display:flex;align-items:center;gap:8px;min-width:0">
              <span class="name" :title="d.custom_name ? ('原代号：' + (d.dev_label || d.dev) + '（悬停可随时查看，终端找盘用）') : '盘符/槽位代号，点「改名」可起个好认的名字'">{{ diskName(d) }}</span>
              <button
                class="badge grade-btn"
                :class="d.asleep ? 'b-muted' : gradeCls(d)"
                :title="d.asleep
                  ? '休眠中，点了看说明'
                  : ('健康分级：' + gradeLabel(d) + '（依据：' + (d.health_grade?.source || 'SMART') + '）· 点开看详细依据与缺陷趋势')"
                @click="openGrade(d)"
              >
                {{ d.asleep ? '休眠' : gradeLabel(d) }}
                <span v-if="!d.asleep && trendUp(d)" class="grade-up" :title="'相比上次采样新增缺陷 ' + trendUp(d) + ' 项'">↑{{ trendUp(d) }}</span>
              </button>
            </div>
            <div style="display:flex;align-items:center;gap:6px;flex:0 0 auto">
              <button class="btn-mini" title="给这块盘起个好认的名字（如「数据盘1」），重启不丢" @click="openRename(d)">改名</button>
            <button
              v-if="d.locate_supported && d.slot"
              class="btn-mini"
              :data-locate-slot="d.slot"
              :disabled="locateBusy[d.slot]"
              @click="raidLocate(d.slot!)"
            >{{ locateBusy[d.slot] ? '定位中…' : (locateOn[d.slot] ? '停止闪灯' : '定位闪灯') }}</button>
            </div>
          </div>

          <!-- 健康分级已收进标题栏徽章的弹窗（点徽章看依据 + 缺陷趋势），卡内不再重复一行 -->

          <!-- 身份 3 行：认盘用，多块盘并排也能横着比 -->
          <div class="kv"><span class="k" title="硬盘的品牌与完整型号名称">型号</span><span class="v">{{ modelText(d) }}</span></div>
          <div class="kv"><span class="k" :title="capTip(d)">容量 / 类型</span><span class="v">{{ capText(d) }}</span></div>
          <div class="kv"><span class="k" title="机械盘每分钟转数；SSD 无转动，显示为固态">转速</span><span class="v">{{ d.rpm || ((d.rota == '1' || d.type === 'sas') ? '—' : '固态(SSD)') }}</span></div>

          <!-- 对比条：只放「能横着比」的少数指标（温度 / 缺陷或寿命 / 已用时长）；
               细分计数、读写量、通道槽位、SN 全部在徽章弹窗里 -->
          <div class="cmp-grid">
            <div v-for="c in compareCells(d)" :key="c.k" class="cmp-cell" :title="c.tip">
              <span class="cmp-k">{{ c.label }}</span>
              <span class="cmp-v" :class="{ hot: c.hot }">{{ c.value }}</span>
            </div>
          </div>

          <div class="temp-bar" :title="'温度占该盘高温线（' + tempTrip(d) + '℃）的比例，横着比一眼看出谁更热、谁更接近上限'">
            <div
              class="temp-fill"
              :style="{ width: (d.temp != null ? Math.min(d.temp / (d.temp_trip || 60) * 100, 100) : 0) + '%', background: tempColor(d.temp, d.temp_trip || 60) }"
            />
            <span class="temp-bar-label">{{ d.temp != null ? Math.round(d.temp) + '℃' : '—' }}</span>
          </div>

          <!-- v2.3.0 3.4 收尾：原先这里的 SAS/NVMe/ATA 累计计数（缺陷扇区、待处理、
               非介质错误、读写错误、已用寿命、剩余备用、临界告警、读写量）已全部移进
               「健康详情」弹窗——它们要么只在出问题时才非 0、要么需要连着上下文看，
               摆在卡面上只占地方、还不便于横向比较；卡面只留上面的对比条。 -->

          <!-- 硬盘自检面板 -->
          <div
            v-if="jobs[d.dev] && jobs[d.dev].state === 'running'"
            class="disk-test-section"
            :class="{ 'disk-test-min': minMap[d.dev] }"
          >
            <template v-if="minMap[d.dev]">
              <AppIcon name="pulse" />硬盘自检 <b>{{ d.dev }}</b> · {{ jobTypeName(jobs[d.dev].type) }}
              <span class="pct">{{ pctOf(d.dev) }}%</span>
              <button class="btn-mini" @click="minMap[d.dev] = false">展开</button>
              <button class="btn-mini" @click="abortDiskTest(d.dev)">中止</button>
              <span class="disk-test-msg">{{ stateMsg[d.dev] }}</span>
            </template>
            <template v-else>
              <div class="disk-test-title">
                <AppIcon name="pulse" />硬盘自检 · {{ jobTypeName(jobs[d.dev].type) }} · {{ d.dev }}
                <button class="btn-mini" style="margin-left:auto" title="收起面板，后台继续扫描" @click="minMap[d.dev] = true">最小化</button>
              </div>
              <div class="disk-test-msg">
                {{ jobs[d.dev].message }} · 已运行 <span>{{ fmtSec(elapsedOf(d.dev)) }}</span>{{ etaOf(d.dev).remain }}<span v-if="etaOf(d.dev).hint">{{ etaOf(d.dev).hint }}</span>
              </div>
              <div v-if="jobs[d.dev].progress != null" class="disk-test-progress">
                <div class="disk-test-progress-fill" :style="{ width: pctOf(d.dev) + '%' }" />
              </div>
              <div v-if="jobs[d.dev].progress != null" class="disk-test-pct" style="text-align:right;font-size:12px;color:var(--primary);font-weight:600;margin-top:3px">{{ pctOf(d.dev) }}%</div>

              <template v-if="isGridJob(d.dev)">
                <div class="surface-grid">
                  <i v-for="(c, i) in gridCells(d.dev)" :key="i" :class="c" />
                </div>
                <div class="surface-legend">
                  <span><i class="sg-cell ok" />正常</span><span><i class="sg-cell" />未扫</span>
                  <span><i class="sg-cell bad" />坏块</span><span><i class="sg-cell cur" />扫描位置</span>
                </div>
              </template>

              <div class="disk-test-actions">
                <button class="btn-mini" @click="abortDiskTest(d.dev)">中止</button>
                <span class="disk-test-msg" :class="{ error: stateErr[d.dev] }">{{ stateMsg[d.dev] }}</span>
              </div>
            </template>
          </div>

          <div v-else-if="jobs[d.dev] && (jobs[d.dev].state === 'done' || jobs[d.dev].state === 'error' || jobs[d.dev].state === 'aborted')" class="disk-test-section">
            <div class="disk-test-title">
              <AppIcon name="pulse" />硬盘自检 · {{ jobs[d.dev].state === 'done' ? '完成' : (jobs[d.dev].state === 'error' ? '失败' : '中止') }}
            </div>
            <div class="disk-test-msg" :class="jobs[d.dev].state === 'done' ? 'done' : (jobs[d.dev].state === 'error' ? 'error' : '')">
              {{ jobs[d.dev].message }}<template v-if="jobs[d.dev].result && jobs[d.dev].result!.read_errors !== undefined"> · 读错误 {{ jobs[d.dev].result!.read_errors }} / 写错误 {{ jobs[d.dev].result!.write_errors }} / 可纠正 {{ jobs[d.dev].result!.corrected }}</template><template v-if="jobs[d.dev].bad_blocks && jobs[d.dev].bad_blocks!.length"> · <b style="color:var(--danger)">坏块 {{ jobs[d.dev].bad_blocks!.length }} 个</b></template>
            </div>
            <div class="disk-test-actions">
              <button class="btn-mini" @click="clearDiskTest(d.dev)">清除</button>
            </div>
          </div>

          <div v-else-if="!d.raid_only" class="disk-test-section">
            <div class="disk-test-title"><AppIcon name="pulse" />硬盘自检</div>
            <div class="disk-test-btns">
              <button class="btn-mini" @click="openPick(d)">硬盘自检</button>
              <button class="btn-mini" @click="showDiskTestHistory(d.dev)">自检记录</button>
            </div>
            <div class="disk-test-msg" :class="{ error: stateErr[d.dev] }">{{ stateMsg[d.dev] }}</div>
          </div>
          <div v-else class="disk-test-section">
            <div class="disk-test-title"><AppIcon name="pulse" />硬盘自检</div>
            <div class="disk-test-msg">物理盘由阵列卡接管，nasdeck 无法在其上跑自检（无 /dev 节点）</div>
          </div>
        </div>
      </div>
    </template>

    <!-- ===== 健康分级详情（v2.3.0 3.4）：点硬盘名旁边的徽章打开 ===== -->
    <div v-if="gradeDisk" class="modal-overlay show" @click.self="gradeDev = null">
      <div class="modal-box" style="max-width:700px;max-height:90vh">
        <div class="modal-title">健康详情 · {{ diskName(gradeDisk) }}</div>
        <div class="modal-body hd-body">
          <div class="hd-id">
            <b>{{ diskName(gradeDisk) }}</b>
            <span v-if="gradeDisk.custom_name" class="sub-name">{{ gradeDisk.dev_label || gradeDisk.dev }}</span>
            <span>{{ gradeDisk.model ? modelText(gradeDisk) : '未知型号' }}</span>
            <span :title="capTip(gradeDisk)">{{ sizeText(gradeDisk) }}</span>
            <span v-if="gradeDisk.devs && gradeDisk.devs.length > 1" class="hd-dual" :title="capTip(gradeDisk)">
              双磁臂 · 整盘 {{ gradeDisk.size_total || '约两倍' }}
            </span>
            <!-- 温度是实时读数，跟下面的「累计计数」不是一类东西，单独放标题栏这一行 -->
            <span
              class="hd-temp"
              :class="{ hot: tempHot(gradeDisk) }"
              :title="'当前温度是实时读数，几秒就会变，不参与缺陷累计、也不跟上次比；该盘高温线 ' + tempTrip(gradeDisk) + '℃（超过会判为「注意」）'"
            >
              当前温度 <b>{{ gradeDisk.temp != null ? gradeDisk.temp + '℃' : 'N/A' }}</b>
              <span class="hd-trip">&nbsp;· 高温线 {{ tempTrip(gradeDisk) }}℃</span>
            </span>
          </div>

          <div class="hd-grade" :style="{ borderLeftColor: gradeColor(gradeDisk) }">
            <span class="hd-dot" :style="{ background: gradeColor(gradeDisk) }" />
            <div>
              <div class="hd-level" :style="{ color: gradeColor(gradeDisk) }">
                {{ gradeLabel(gradeDisk) }}
                <span v-if="trendUp(gradeDisk)" style="font-size:12px;font-weight:600;color:var(--orange);margin-left:6px">
                  相比上次 +{{ trendUp(gradeDisk) }}
                </span>
              </div>
              <div class="hd-explain">{{ gradeExplain(gradeDisk) }}</div>
            </div>
          </div>

          <div class="hd-sec">
            <h4>为什么是这一档</h4>
            <ul v-if="(gradeDisk.health_grade?.reasons || []).length" class="hd-reasons">
              <li v-for="(r, i) in (gradeDisk.health_grade?.reasons || [])" :key="i">{{ r }}</li>
            </ul>
            <div v-else class="hd-none">没有发现任何异常信号——各项缺陷计数都为 0，温度也在安全范围内。</div>
          </div>

          <div class="hd-sec">
            <h4>缺陷趋势</h4>
            <div v-if="gradeDisk.health_trend && gradeDisk.health_trend.prev_ts" class="hd-trend">
              较上次采样（{{ fmtAge(gradeDisk.health_trend.prev_ts) }}）：
              <b v-if="trendParts(gradeDisk).length" class="up">{{ trendParts(gradeDisk).join('、') }}</b>
              <b v-else class="flat">无新增缺陷</b>
            </div>
            <div v-else-if="counterRows(gradeDisk).length" class="hd-none">
              首次快照已记录，现在还没有「上一次」可比。<b>下次采样（约 6 小时后）</b>起这里会显示「比上次多了多少」——数字只增不减，一旦开始涨就说明盘在变坏。
            </div>
            <div v-else class="hd-none">这块盘没有可跟踪的缺陷计数，暂时做不了趋势对比。</div>
            <div v-if="counterRows(gradeDisk).length" class="hd-note">
              后台每 6 小时记一次，保留 180 天；只跟<b>上一次</b>比，所以这里看到的是「最近 6 小时新长出来的缺陷」。
            </div>
          </div>

          <div class="hd-sec">
            <h4>各项缺陷 / 寿命计数<span class="hd-h4note">累计值 · 只增不减</span></h4>
            <div v-if="counterRows(gradeDisk).length" class="hd-counters">
              <div v-for="c in counterRows(gradeDisk)" :key="c.k" class="hd-counter" :title="c.tip">
                <span class="ck">{{ c.name }}</span>
                <span class="cv" :class="{ hot: c.hot }">{{ c.v }}{{ c.unit }}</span>
              </div>
            </div>
            <div v-else class="hd-none">没有可显示的计数（休眠中或读不到 SMART）。</div>
          </div>

          <div class="hd-sec">
            <h4>硬盘档案<span class="hd-h4note">认盘 / 报修用 · 不参与分级</span></h4>
            <div class="hd-counters">
              <div class="hd-counter" title="硬盘出厂唯一编号，报修、查保修要用它"><span class="ck">序列号</span><span class="cv" :title="gradeDisk.serial || '—'">{{ gradeDisk.serial || '—' }}</span></div>
              <div class="hd-counter" title="这块盘接在哪：阵列卡通道 / 主板直连"><span class="ck">通道</span><span class="cv" :title="gradeDisk.channel || '—'">{{ gradeDisk.channel || '—' }}</span></div>
              <div v-if="gradeDisk.slot" class="hd-counter" title="阵列卡上的物理槽位（对应机箱里的盘位）"><span class="ck">槽位</span><span class="cv" :title="gradeDisk.slot">{{ gradeDisk.slot }}</span></div>
              <div class="hd-counter" title="机械盘每分钟转数；固态盘没有转动部件"><span class="ck">转速</span><span class="cv">{{ gradeDisk.rpm || ((gradeDisk.rota == '1' || gradeDisk.type === 'sas') ? '—' : '固态(SSD)') }}</span></div>
              <div class="hd-counter" title="累计通电运行时间"><span class="ck">已用时长</span><span class="cv">{{ fmtHours(gradeDisk.power_on_hours) }}</span></div>
              <div v-if="gradeDisk.data_units_read" class="hd-counter" title="开机以来累计读取总量（悬停看精确值）"><span class="ck">累计读取量</span><span class="cv" :title="gradeDisk.data_units_read">{{ dataUnitShort(gradeDisk.data_units_read) }}</span></div>
              <div v-if="gradeDisk.data_units_written" class="hd-counter" title="开机以来累计写入总量（悬停看精确值）"><span class="ck">累计写入量</span><span class="cv" :title="gradeDisk.data_units_written">{{ dataUnitShort(gradeDisk.data_units_written) }}</span></div>
              <div v-if="gradeDisk.critical_warning != null" class="hd-counter" title="NVMe 告警标志：0x00 表示一切正常"><span class="ck">临界告警</span><span class="cv" :class="{ hot: cwBad(gradeDisk) }">{{ cwBad(gradeDisk) ? ('0x' + gradeDisk.critical_warning + ' 告警') : '正常' }}</span></div>
            </div>
            <div v-if="gradeDisk.devs && gradeDisk.devs.length > 1" class="hd-note">
              <b>双磁臂硬盘</b>：两个执行器共用一个盘体，系统里会看到 <b>{{ gradeDisk.dev_label }}</b> 两个逻辑盘。已合并成一张卡，各项数值取两臂中<b>更差的一侧</b>；自检、定位等操作默认作用在 <b>{{ gradeDisk.dev }}</b>。
            </div>
          </div>

          <div class="hd-src">
            判定依据来自：{{ gradeDisk.health_grade?.source || '硬盘自身 SMART' }}。全部是硬盘和阵列卡自己上报的数据，nasdeck 只做汇总分级，不猜、不做推断。
          </div>
        </div>
        <div class="modal-actions">
          <button class="btn" @click="gradeDev = null">关闭</button>
        </div>
      </div>
    </div>

    <!-- ===== 自检方式选择（旧页 appPick） ===== -->
    <div v-if="pickOpen" class="modal-overlay show" @click.self="pickOpen = false">
      <div class="modal-box">
        <div class="modal-title">对 {{ pickModel }}（/dev/{{ pickDev }}）执行哪种自检？</div>
        <div class="modal-body">
          <button
            v-for="o in pickOptions"
            :key="o.type"
            class="pick-opt"
            :class="{ 'pick-opt-disabled': o.disabled }"
            :disabled="o.disabled"
            @click="choosePick(o.type)"
          >
            <span class="pick-opt-t">{{ o.label }}</span>
            <span class="pick-opt-d">{{ o.desc }}</span>
            <span v-if="o.note" class="pick-opt-note">{{ o.note }}</span>
          </button>
        </div>
        <div class="modal-actions">
          <button class="btn" @click="pickOpen = false">取消</button>
        </div>
      </div>
    </div>

    <!-- ===== 二次确认（旧页 appConfirm） ===== -->
    <div v-if="confirmOpen" class="modal-overlay show" @click.self="closeConfirm(false)">
      <div class="modal-box">
        <div class="modal-title">{{ confirmTitle }}</div>
        <div class="modal-body" style="white-space:pre-wrap">{{ confirmBody }}</div>
        <div class="modal-actions">
          <button class="btn" @click="closeConfirm(false)">{{ confirmCancel }}</button>
          <button class="btn" :class="confirmDanger ? 'btn-danger' : 'btn-primary'" @click="closeConfirm(true)">{{ confirmOk }}</button>
        </div>
      </div>
    </div>

    <!-- ===== 自检记录（旧页 showDiskTestHistory） ===== -->
    <div v-if="histOpen" class="modal-overlay show modal-wide" @click.self="histOpen = false">
      <div class="modal-box">
        <div class="modal-title">硬盘自检记录{{ histDev ? ' · /dev/' + histDev : '' }}</div>
        <div class="modal-body">
          <div v-if="histLoading" class="log-empty">加载中…</div>
          <div v-else-if="histErr" class="log-empty">{{ histErr }}</div>
          <div v-else-if="!histRows.length" class="log-empty">暂无自检记录</div>
          <template v-else>
            <div class="hist-head">
              <b class="c1">盘</b><span class="c2">类型</span><span class="c3">结果</span>
              <span class="c4">坏块</span><span class="c5">用时</span>
              <span class="c6">说明</span><span class="c7">时间</span>
            </div>
            <div v-for="(h, i) in histRows" :key="i" class="hist-row">
              <b class="c1">{{ h.dev || '' }}</b>
              <span class="c2">{{ histTypeName(h.type) }}</span>
              <span class="c3" :style="{ color: histStateText(h.state).color }">{{ histStateText(h.state).text }}</span>
              <span class="c4">
                <span v-if="(h.bad_blocks || 0) > 0" style="color:var(--danger)">坏块 {{ h.bad_blocks }}</span>
                <span v-else>坏块 0</span>
              </span>
              <span class="c5">{{ fmtSec(h.elapsed || 0) }}</span>
              <span class="c6" :title="histDesc(h).title">
                <template v-for="(s, si) in histDesc(h).segs" :key="si">
                  <span
                    :style="{ color: s.cls === 'danger' ? 'var(--danger)' : (s.cls === 'dim' ? 'var(--text-3)' : 'inherit') }"
                  >{{ s.text }}</span><span v-if="si < histDesc(h).segs.length - 1"> · </span>
                </template>
              </span>
              <span class="c7">{{ histTime(h.finished_at) }}</span>
            </div>
            <div class="hist-foot">仅保留最近 50 条 · 应用重启后仍可回查</div>
          </template>
        </div>
        <div class="modal-actions">
          <button class="btn" @click="histOpen = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- ===== 改名弹窗（v2.3.0 3.8） ===== -->
    <div v-if="renameOpen && renameTarget" class="modal-overlay show" @click.self="renameOpen = false">
      <div class="modal-box" style="max-width:420px">
        <div class="modal-title">改名 · {{ renameTarget.dev_label || renameTarget.dev }}</div>
        <div class="modal-body">
          <div style="font-size:13px;color:var(--text-2);margin-bottom:10px">
            给这块盘起个好认的名字（如「数据盘1」「仓库盘」），卡片、健康详情都会优先显示它；原代号保留作副标。留空点保存无效，<b>清除</b>恢复显示代号。重启不丢。
          </div>
          <input
            v-model="renameValue"
            class="rename-input"
            type="text"
            maxlength="40"
            placeholder="如：数据盘1"
            @keyup.enter="saveRename()"
          />
          <div v-if="renameErr" style="color:var(--danger);font-size:12px;margin-top:8px">{{ renameErr }}</div>
        </div>
        <div class="modal-actions">
          <button class="btn" :disabled="renameBusy" @click="renameOpen = false">取消</button>
          <button v-if="renameTarget.custom_name" class="btn" :disabled="renameBusy" @click="saveRename(true)">清除</button>
          <button class="btn btn-primary" :disabled="renameBusy || !renameValue.trim()" @click="saveRename()">{{ renameBusy ? '保存中…' : '保存' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
