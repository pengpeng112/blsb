<!-- PT-03：传染病上报专属监控座舱（/infectious 侧栏直达）。
     五页签：运行总览 / 13 接口全景矩阵 / 守护体检与企微晨报 / 提交回执明细 /
     对账与数据接入。数据源=平台台账 + 生产状态只读增量同步（对源目录 100% 只读）。
     受理≠国家平台终审：主数字口径如实标注"前置机受理"。视觉对齐 DrugTrace 座舱
     （eyebrow+图标头+页签+双栏），零工程黑话。startTab prop 供 SSR 逐页签断言。 -->
<template>
  <div class="inf-page">
    <header class="inf-header">
      <div class="inf-heading">
        <div class="inf-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M12 3l8 3v5c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-3z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" /><path d="M9 12l2 2 4-4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg></div>
        <div>
          <p class="inf-eyebrow">国家传染病智能监测预警前置软件 · 13 类临床文书实时监测</p>
          <h2>传染病上报</h2>
          <p class="inf-desc">班次推送、父表门禁、守护体检与日终对账，一站看清全链路。</p>
        </div>
      </div>
      <div class="inf-tools">
        <span class="inf-badge" :class="watchdogTone" v-if="watchdog">
          <i></i>守护 {{ watchdogLevelCn }}
        </span>
        <span class="inf-badge relay" v-if="overview">
          <i></i>企微隧道{{ overview.wecom_relay_ok === true ? '正常'
            : (overview.wecom_relay_ok === false ? '不可达' : '待探测') }}
        </span>
        <button class="inf-sync" @click="doSync" :disabled="syncing">
          {{ syncing ? '正在同步…' : '同步最新状态' }}</button>
        <button class="inf-refresh" @click="loadAll">刷新</button>
        <label class="inf-auto"><input type="checkbox" v-model="autoRefresh" /> 每 60 秒自动刷新</label>
      </div>
    </header>

    <div v-if="syncMsg" class="inf-notice" :class="syncMsgTone">{{ syncMsg }}</div>
    <!-- 页签常显（首屏数据加载中也可切换页签） -->
    <nav class="inf-tabs" aria-label="传染病监控功能">
      <button v-for="tb in TABS" :key="tb.key" :class="{ active: tab === tb.key }"
              @click="tab = tb.key">{{ tb.name }}</button>
    </nav>
    <LoadingBlock v-if="loading && !overview" />
    <ErrorState v-else-if="error && !overview" :message="error" @retry="loadAll" />
    <template v-else>

      <!-- ============ 页签 1：运行总览 ============ -->
      <section v-if="tab === 'overview'" class="inf-pane">
        <div class="inf-metrics">
          <button v-for="c in metricCards" :key="c.key" class="inf-metric" :class="c.tone"
                  @click="c.jump && (tab = c.jump)">
            <span class="m-top">{{ c.title }}</span>
            <span class="m-val">{{ c.value }}<small> 条</small></span>
            <span class="m-note">{{ c.note }}</span>
          </button>
        </div>
        <div class="inf-columns">
          <div class="inf-main">
            <div class="inf-card">
              <b>近 14 天受理与异常趋势</b>
              <p class="inf-muted">每日合计：已受理 / 等待父表 / 退回 / 未知</p>
              <div class="inf-chart" role="img" aria-label="14 天每日受理与异常数量柱状图">
                <div v-for="d in trendDays" :key="d.date" class="inf-chart-day">
                  <span class="inf-chart-num">{{ d.total }}</span>
                  <div class="inf-chart-bar-area">
                    <div class="inf-chart-bar" :style="{ height: d.pct + '%' }">
                      <span v-for="p in d.parts" :key="p.key" class="inf-seg"
                            :class="p.key"
                            :style="{ height: d.total ? p.v / d.total * 100 + '%' : '0%' }"
                            :title="d.date + ' ' + p.label + ' ' + p.v"></span>
                    </div>
                  </div>
                  <span class="inf-chart-date">{{ d.date.slice(5) }}</span>
                </div>
              </div>
              <div class="inf-legend">
                <span><i class="ok"></i>已受理</span>
                <span><i class="wait"></i>等待父表</span>
                <span><i class="bad"></i>被退回</span>
                <span><i class="unk"></i>未知</span>
              </div>
            </div>
            <div class="inf-card">
              <b>回执错误码分布（近 {{ overview?.days ?? 7 }} 天）</b>
              <p class="inf-muted">按前置机返回码聚合，附中文含义</p>
              <table v-if="overview?.error_codes?.length" class="inf-mini-table">
                <thead><tr><th>错误码</th><th>条数</th><th>含义与建议</th></tr></thead>
                <tbody>
                  <tr v-for="e in overview.error_codes" :key="e.code">
                    <td><code>{{ e.code }}</code></td>
                    <td>{{ e.count }}</td>
                    <td class="inf-muted">{{ e.meaning }}</td>
                  </tr>
                </tbody>
              </table>
              <EmptyState v-else text="窗口内无错误回执（全部受理成功）" />
            </div>
          </div>
          <aside class="inf-side">
            <div class="inf-card">
              <b>需要关注</b>
              <p v-if="!attention.length" class="inf-muted">当前无待关注事项。</p>
              <button v-for="a in attention" :key="a.key" class="inf-attention"
                      @click="a.tab && (tab = a.tab)">
                <i :class="a.tone"></i>
                <div><strong>{{ a.title }}</strong><p>{{ a.detail }}</p></div>
                <span>›</span>
              </button>
            </div>
            <div class="inf-card">
              <b>推送班次与日终对账</b>
              <ul class="inf-facts">
                <li>最近班次：{{ fmt(overview?.last_shift_at) || '暂无记录' }}</li>
                <li>数据观测截至：{{ fmt(overview?.observed_until) || '—' }}</li>
                <li>最大水位滞后：{{ overview?.max_watermark_lag_hours != null
                  ? overview.max_watermark_lag_hours + ' 小时' : '—' }}
                  <span v-if="overview?.max_watermark_lag_hours != null
                    && overview.max_watermark_lag_hours > 26"
                    class="inf-warn-tag">已超 26 小时阈值</span></li>
                <li>守护最近检查：{{ fmt(overview?.watchdog_checked_at) || '—' }}</li>
                <li>状态目录同步：{{ overview?.sync_configured ? '已配置（只读增量）'
                  : '未配置（展示导入副本数据）' }}</li>
              </ul>
            </div>
          </aside>
        </div>
      </section>

      <!-- ============ 页签 2：13 接口全景矩阵 ============ -->
      <section v-else-if="tab === 'tables'" class="inf-pane">
        <div class="inf-muted inf-pane-note">
          共 {{ tablesData?.total_tables ?? 13 }} 张在推接口 · 按临床业务域分组 ·
          水位滞后与最近一班一目了然 · 点「查明细」跳到回执页签
        </div>
        <LoadingBlock v-if="tablesLoading && !tablesData" />
        <div v-for="g in tablesData?.groups ?? []" :key="g.group" class="inf-card">
          <b>{{ g.group }} <span class="inf-muted">（{{ g.tables.length }} 张）</span></b>
          <table class="inf-mini-table">
            <thead>
              <tr><th>文书</th><th>前置机端点</th><th>主子依赖</th>
                <th>数据水位</th><th>滞后</th>
                <th>最近一班 扫/发/等父/尾差/失败</th>
                <th>近 {{ overview?.days ?? 7 }} 天受理</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="r in g.tables" :key="r.table">
                <td><b>{{ r.doc_type_name }}</b><code class="inf-code">{{ r.doc_type_code }}</code></td>
                <td class="inf-muted">{{ r.endpoint }}</td>
                <td>
                  <span v-if="r.parent_name" class="inf-dep">依赖 {{ r.parent_name }}</span>
                  <span v-else class="inf-muted">主表</span>
                </td>
                <td>{{ fmt(r.watermark) || '—' }}</td>
                <td>
                  <span class="inf-lag" :class="lagTone(r.lag_hours)">
                    <i :style="{ width: lagPct(r.lag_hours) + '%' }"></i></span>
                  <span class="inf-muted">{{ r.lag_hours != null ? r.lag_hours + 'h' : '—' }}</span>
                </td>
                <td class="inf-muted">{{ r.shift.rows }} / {{ r.shift.sent }} /
                  {{ r.shift.parent_wait }} / {{ r.shift.skipped_invalid }} /
                  {{ r.shift.failed }}</td>
                <td>{{ r.accepted }}</td>
                <td><button class="ghost" @click="gotoDetail(r.table)">查明细</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- ============ 页签 3：守护体检与企微晨报 ============ -->
      <section v-else-if="tab === 'watchdog'" class="inf-pane">
        <div class="inf-checks">
          <div v-for="c in checkCards" :key="c.key" class="inf-check" :class="c.tone">
            <span class="ck-name">{{ c.name }}</span>
            <span class="ck-level">{{ c.levelCn }}</span>
            <p class="ck-detail">{{ c.detail || '正常' }}</p>
            <span class="ck-ms">{{ c.ms }} ms</span>
          </div>
        </div>
        <div class="inf-columns">
          <div class="inf-card">
            <b>近期守护告警时间线</b>
            <p v-if="!watchdog?.recent_alerts?.length" class="inf-muted">
              暂无告警流水（导入副本或配置同步后显示）</p>
            <ul v-else class="inf-alerts">
              <li v-for="(a, i) in watchdog.recent_alerts" :key="i">
                <span class="inf-alert-time">{{ a.ts }}</span>
                <StatusTag :status="a.level || 'INFO'" />
                <span class="inf-alert-subject">{{ a.subject }}</span>
              </li>
            </ul>
          </div>
          <div class="inf-card">
            <b>每日 08:00 晨报</b>
            <p v-if="!watchdog?.daily?.text" class="inf-muted">暂无晨报（同步或导入后显示）</p>
            <pre v-else class="inf-daily">{{ watchdog.daily.text }}</pre>
          </div>
        </div>
      </section>

      <!-- ============ 页签 4：提交回执明细 ============ -->
      <section v-else-if="tab === 'detail'" class="inf-pane">
        <FilterBar :busy="detailLoading" @search="reloadDetail" @reset="resetDetail">
          <select v-model="detailBucket">
            <option value="">全部状态</option>
            <option value="ACCEPTED">已受理</option>
            <option value="PENDING">等待父表受理</option>
            <option value="REJECTED">被退回</option>
            <option value="UNKNOWN">结果未知</option>
          </select>
          <select v-model="detailTable">
            <option value="">全部 13 类文书</option>
            <option v-for="t in TABLE_OPTIONS" :key="t.key" :value="t.key">
              {{ t.name }}（{{ t.key }}）</option>
          </select>
          <input v-model="detailQ" placeholder="记录键前缀" />
        </FilterBar>
        <div class="inf-muted meta-line">
          共约 {{ detailPage?.total_estimate ?? '—' }} 条 ·
          数据源：{{ detailPage?.data_source === 'external_ledger' ? '导入副本' : '平台' }}
        </div>
        <DataTable :columns="detailCols" :rows="detailRows"
                   :loading="detailLoading && !detailRows.length"
                   :error="detailError" @retry="reloadDetail"
                   :has-more="!!detailCursor" :loading-more="detailLoadingMore"
                   @load-more="loadMoreDetail" @row-click="openDetail"
                   empty-text="无匹配记录" />
        <Drawer :open="!!extDetail" :title="extDetailTitle"
                @update:open="extDetail = null" @close="extDetail = null">
          <template v-if="extDetail">
            <h4>文书：{{ extDetail.doc_type_name || '未分类文书' }}
              <code v-if="extDetail.doc_type_code">（{{ extDetail.doc_type_code }}）</code></h4>
            <p>记录键 <code>{{ extDetail.record_key }}</code></p>
            <p>当前状态：<StatusTag :status="extDetail.bucket || 'UNKNOWN'" />
              <span class="inf-muted">{{ bucketCn(extDetail.bucket) }}</span></p>
            <p>证据等级：{{ evidenceCn(extDetail.evidence_level) }}</p>
            <ul class="ext-facts">
              <li>源业务时间：{{ fmt(extDetail.source_op_time) || '（回执未携带，未知）' }}</li>
              <li>最近推送时间：{{ fmt(extDetail.last_attempt_at) || '（未知，不冒充）' }}</li>
              <li>累计尝试（可见下界）：{{ extDetail.attempts_lower_bound }} 次</li>
              <li>来源实例：{{ extDetail.source_instance }}</li>
            </ul>
            <template v-if="extDetail.last_error_code || extDetail.last_error_message">
              <b>异常诊断</b>
              <p class="diag">{{ errorCodeCn(extDetail.last_error_code, extDetail.raw_state) }}</p>
              <p class="inf-muted" v-if="extDetail.last_error_message">
                详情：{{ extDetail.last_error_message }}</p>
            </template>
          </template>
        </Drawer>
      </section>

      <!-- ============ 页签 5：对账与数据接入 ============ -->
      <section v-else-if="tab === 'reconcile'" class="inf-pane">
        <div class="inf-card">
          <b>日终对账（00:05 补推班）近期记录</b>
          <p v-if="!reconcileData?.reconcile?.length" class="inf-muted">
            暂无对账快照（导入 reconcile-*.json 或配置只读同步后显示）</p>
          <table v-else class="inf-mini-table">
            <thead><tr><th>文书</th><th>对账日</th><th>补推</th>
              <th>等待父受理</th><th>失败</th></tr></thead>
            <tbody>
              <tr v-for="r in reconcileData.reconcile" :key="r.table + r.day">
                <td>{{ r.doc_type_name }}</td><td>{{ r.day }}</td>
                <td>{{ r.repushed }}</td><td>{{ r.parent_wait }}</td>
                <td>{{ r.failed }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="inf-card">
          <b>平台影子 vs 外部台账差异（7 类）</b>
          <pre v-if="reconcileData?.shadow_diff" class="inf-daily">{{ JSON.stringify(reconcileData.shadow_diff, null, 2).slice(0, 3000) }}</pre>
          <p v-else class="inf-muted">暂无影子差异汇总。</p>
        </div>
        <div class="inf-card">
          <b>状态副本导入与只读同步</b>
          <p class="inf-muted">推荐在「业务详情 → 数据接入」上传副本（默认混合时区）；
            生产环境配置只读挂载目录后，点「同步最新状态」自动增量吸收（对源目录 100% 只读）。</p>
          <p class="inf-muted">当前同步状态：
            {{ overview?.sync_configured ? '已配置只读挂载目录' : '未配置（BLYB_INF_PUSHER_STATE_DIR / BLYB_INF_WATCHDOG_STATE_DIR）' }}</p>
        </div>
      </section>
    </template>

  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  infOverview, infTables, infWatchdog, infReconcile, infSync,
} from '../api/infectious'
import type { InfOverview, InfTableGroup, InfWatchdog, InfReconcile,
  InfSyncResult, InfTableRow } from '../api/infectious'
import PageHeader from '../components/PageHeader.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import FilterBar from '../components/FilterBar.vue'
import DataTable from '../components/DataTable.vue'
import Drawer from '../components/Drawer.vue'
import StatusTag from '../components/StatusTag.vue'
import { toast } from '../components/Toast'
import { ledgerSearch, ledgerDetail } from '../api/index'
import { formatHospitalTime } from '../utils/time'

// PT-03：13 类文书下拉选项（与后端 INF_TABLES 同源口径）
const TABLE_OPTIONS = [
  { key: 'admission/', name: '住院病案首页' },
  { key: 'admission_info/', name: '入院记录' },
  { key: 'discharge_info/', name: '出院记录' },
  { key: 'death/', name: '死亡记录' },
  { key: 'first_course/', name: '首次病程记录' },
  { key: 'daily_course/', name: '日常病程记录' },
  { key: 'exam/', name: '检查报告主表' },
  { key: 'exam_item/', name: '检查报告项目' },
  { key: 'ex_lab/', name: '检验报告主表' },
  { key: 'ex_lab_item/', name: '检验报告项目' },
  { key: 'order/', name: '医嘱处方主表' },
  { key: 'order_item/', name: '医嘱药品明细' },
  { key: 'vital_signs/', name: '生命体征记录' },
]
const CHECK_NAMES: Record<string, string> = {
  c1_freshness: 'C1 班次新鲜度', c2_lastrun_error: 'C2 班次含异常表',
  c3_skip_streak: 'C3 班次被跳过', c4_lock_hang: 'C4 推送锁悬挂',
  c5_push_failures: 'C5 推送失败', c6_reconcile: 'C6 日终对账',
  c7_pusher_alerts: 'C7 对账缺口告警', c8_watermark: 'C8 水位停滞',
  c9_disk: 'C9 磁盘空间', c10_container: 'C10 推送容器',
  c11_relay_tunnel: 'C11 企微隧道真达',
}
const LEVEL_CN: Record<string, string> = {
  OK: '正常', WARN: '警告', CRITICAL: '严重', ERROR: '错误', DISABLED: '停用',
}
const BUCKET_CN: Record<string, string> = {
  PENDING: '待处理（等待父表受理）', SENDING: '发送中', ACCEPTED: '前置机已受理',
  CONFIRMED: '已确认', PARTIAL: '部分成功', REJECTED: '被退回',
  UNKNOWN: '结果未知', NOT_REPORTABLE: '不上报',
}
const EVIDENCE_CN: Record<string, string> = {
  FRONTEND_ACCEPTED: '前置机回执实证（受理≠国家平台终审）',
  SENT_LEDGER_ONLY: '已发账实证（回执副本未含该条）',
  CLIENT_EVIDENCE: '本地回执实证', PARENT_WAITING: '主子门禁保护中',
  MOCK_ONLY: '演练记录（Mock）', INCONCLUSIVE: '结果不确定',
}

const TABS = [
  { key: 'overview', name: '运行总览' },
  { key: 'tables', name: '13 接口全景矩阵' },
  { key: 'watchdog', name: '守护体检与企微晨报' },
  { key: 'detail', name: '提交回执明细' },
  { key: 'reconcile', name: '对账与数据接入' },
]

const props = defineProps<{ startTab?: string }>()

const tab = ref(props.startTab || 'overview')
watch(() => props.startTab, v => { if (v) tab.value = v })

const loading = ref(true)
const error = ref('')
const overview = ref<InfOverview | null>(null)
const tablesData = ref<{ days: number, groups: InfTableGroup[],
  total_tables: number } | null>(null)
const tablesLoading = ref(false)
const watchdog = ref<InfWatchdog | null>(null)
const reconcileData = ref<InfReconcile | null>(null)
const syncing = ref(false)
const syncMsg = ref('')
const syncMsgTone = ref('ok')
const autoRefresh = ref(false)
let timer: number | undefined

// 页签 4 明细
const detailBucket = ref('')
const detailTable = ref('')
const detailQ = ref('')
const detailRows = ref<any[]>([])
const detailPage = ref<any>(null)
const detailCursor = ref<string | null>(null)
const detailLoading = ref(false)
const detailLoadingMore = ref(false)
const detailError = ref('')
const extDetail = ref<any>(null)
const extDetailTitle = ref('')
const detailCols = [
  { key: 'business_key', title: '记录键' },
  { key: 'bucket', title: '状态' },
  { key: 'raw_state', title: '阶段' },
  { key: 'attempts', title: '尝试', align: 'right' as const },
  { key: 'last_error_code', title: '错误码', formatter: (r: any) =>
    r.last_error_code ?? '—' },
  { key: 'updated_at', title: '更新时间', formatter: (r: any) =>
    fmt(r.updated_at) || '—' },
]

const metricCards = computed(() => {
  const b = overview.value?.buckets ?? {}
  return [
    { key: 'accepted', tone: 'ok', title: '前置机已受理',
      value: (b.ACCEPTED ?? 0) + (b.CONFIRMED ?? 0),
      note: '回执实证 result=true（受理≠国家平台终审）', jump: 'detail' },
    { key: 'wait', tone: 'wait', title: '等待父表受理',
      value: b.PENDING ?? 0,
      note: '主子门禁保护中：父单成功后下一班自动补发', jump: 'detail' },
    { key: 'reject', tone: 'bad', title: '接口退回（02/03/04）',
      value: b.REJECTED ?? 0, note: '需核对字段口径或源数据', jump: 'detail' },
    { key: 'invalid', tone: 'muted', title: '必填预检尾差',
      value: clientInvalidEstimate(), note: '本地必填预检未过，进入质量等待',
      jump: 'detail' },
    { key: 'unknown', tone: 'warn', title: '网络/服务待重试',
      value: b.UNKNOWN ?? 0, note: '01/99 与网络异常自动重试', jump: 'detail' },
  ]
})
// 必填尾差：桶级并入 REJECTED（回执阶段细节在明细检索按阶段过滤）——
// 概览卡片给守护快照或班次遥测中的 skipped_invalid 合计（无遥测时回 0）
function clientInvalidEstimate(): number {
  let sum = 0
  for (const g of tablesData.value?.groups ?? []) {
    for (const r of g.tables) sum += r.shift.skipped_invalid
  }
  return sum
}

const attention = computed(() => {
  const out: { key: string, tone: string, title: string, detail: string,
    tab?: string }[] = []
  if ((overview.value?.max_watermark_lag_hours ?? 0) > 26) {
    out.push({ key: 'lag', tone: 'bad', title: '数据水位滞后超 26 小时',
      detail: '疑似取数停滞，跳到全景矩阵查看逐表水位', tab: 'tables' })
  }
  if (overview.value?.watchdog_level === 'CRITICAL'
      || overview.value?.watchdog_level === 'WARN') {
    out.push({ key: 'wd', tone: 'warn',
      title: `守护体检${overview.value.watchdog_level === 'CRITICAL' ? '存在严重项' : '存在警告项'}`,
      detail: '跳到守护体检查看 C1–C11 详情', tab: 'watchdog' })
  }
  if (overview.value?.wecom_relay_ok === false) {
    out.push({ key: 'relay', tone: 'bad', title: '企微隧道不可达',
      detail: '告警外发受阻，恢复后自动补发', tab: 'watchdog' })
  }
  const wait = overview.value?.buckets?.PENDING ?? 0
  if (wait > 300) {
    out.push({ key: 'pw', tone: 'warn', title: `等待父受理积压 ${wait} 条`,
      detail: '交科室补录父单后自动补发', tab: 'detail' })
  }
  return out
})

const watchdogTone = computed(() =>
  overview.value?.watchdog_level === 'OK' ? 'ok'
    : overview.value?.watchdog_level === 'WARN' ? 'warn'
      : overview.value?.watchdog_level === 'CRITICAL' ? 'bad' : 'muted')
const watchdogLevelCn = computed(() =>
  ({ OK: '正常', WARN: '警告', CRITICAL: '严重' })[overview.value?.watchdog_level ?? '']
  ?? overview.value?.watchdog_level ?? '待探测')

const checkCards = computed(() => {
  const checks = watchdog.value?.checks ?? {}
  return Object.keys(CHECK_NAMES).map(k => {
    const c = checks[k]
    const level = c?.level ?? 'OK'
    return { key: k, name: CHECK_NAMES[k], levelCn: LEVEL_CN[level] ?? level,
      tone: level === 'OK' ? 'ok'
        : level === 'WARN' ? 'warn' : level === 'DISABLED' ? 'muted' : 'bad',
      detail: c?.detail ?? '', ms: c?.elapsed_ms ?? 0 }
  })
})

interface TrendDay { date: string, total: number, pct: number,
  parts: { key: string, label: string, v: number }[] }
const trendDays = computed<TrendDay[]>(() => {
  const list = (overview.value?.daily ?? []).slice(-14)
  const max = Math.max(1, ...list.map(d =>
    (d.ACCEPTED ?? 0) + (d.PENDING ?? 0) + (d.REJECTED ?? 0) + (d.UNKNOWN ?? 0)))
  return list.map(d => {
    const parts = [
      { key: 'ok', label: '已受理', v: d.ACCEPTED ?? 0 },
      { key: 'wait', label: '等待父表', v: d.PENDING ?? 0 },
      { key: 'bad', label: '被退回', v: d.REJECTED ?? 0 },
      { key: 'unk', label: '未知', v: d.UNKNOWN ?? 0 },
    ]
    const total = parts.reduce((s, p) => s + p.v, 0)
    return { date: d.date, total,
      pct: Math.round(total / max * 100), parts }
  })
})

function lagTone(h: number | null): string {
  if (h == null) return ''
  if (h <= 6) return 'ok'
  if (h <= 26) return 'warn'
  return 'bad'
}
function lagPct(h: number | null): number {
  if (h == null) return 0
  return Math.min(100, Math.round(h / 26 * 100))
}
function fmt(v?: string | null): string {
  if (!v) return ''
  try { return formatHospitalTime(v) ?? v } catch { return v }
}
function bucketCn(b?: string): string { return BUCKET_CN[b ?? ''] ?? (b ?? '—') }
function evidenceCn(e?: string): string { return EVIDENCE_CN[e ?? ''] ?? (e ?? '—') }
function errorCodeCn(code?: string | null, rawState?: string): string {
  if (rawState === 'PARENT_WAIT') return '等待父表主记录受理：父单成功后下一班自动补发'
  if (rawState === 'CLIENT_INVALID') return '本地必填预检未过：进入质量等待'
  const m: Record<string, string> = {
    '00': '受理成功', '01': '平台服务异常（自动重试）',
    '02': '字典/值域不符', '03': '数据格式错误',
    '04': '平台必填项校验未过：请核查源病历或视图字段',
    '05': '时间格式无法解析', '99': '平台未知异常（自动重试）' }
  return m[String(code ?? '').trim()] ?? '未知错误码'
}

async function loadAll(): Promise<void> {
  loading.value = !overview.value
  error.value = ''
  try {
    const [ov, wd] = await Promise.allSettled([infOverview(7), infWatchdog()])
    if (ov.status === 'fulfilled') overview.value = ov.value
    else error.value = String(ov.reason ?? '总览加载失败')
    if (wd.status === 'fulfilled') watchdog.value = wd.value
    loadTables()
    loadReconcile()
    if (tab.value === 'detail' && !detailRows.value.length) reloadDetail()
  } finally {
    loading.value = false
  }
}
async function loadTables(): Promise<void> {
  tablesLoading.value = true
  try { tablesData.value = await infTables(7) }
  catch { /* 矩阵失败不阻断总览 */ }
  finally { tablesLoading.value = false }
}
async function loadReconcile(): Promise<void> {
  try { reconcileData.value = await infReconcile(7) }
  catch { /* 差异对比失败不阻断 */ }
}
async function doSync(): Promise<void> {
  syncing.value = true
  syncMsg.value = ''
  try {
    const r: InfSyncResult = await infSync()
    if (!r.configured) {
      syncMsg.value = r.reason ?? '未配置实时状态挂载目录'
      syncMsgTone.value = 'muted'
    } else {
      syncMsg.value = `同步完成：扫描 ${r.files_scanned ?? 0} 个文件，`
        + `新增回执 ${r.rows_read ?? 0} 行，新建记录 ${r.records_created ?? 0}，`
        + `快照 ${r.snapshots_parsed ?? 0} 份`
      syncMsgTone.value = 'ok'
      loadAll()
    }
  } catch (e: any) {
    syncMsg.value = `同步失败：${e?.message ?? e}`
    syncMsgTone.value = 'bad'
  } finally {
    syncing.value = false
  }
}
function gotoDetail(table: string): void {
  tab.value = 'detail'
  detailTable.value = table + '/'
  detailBucket.value = ''
  reloadDetail()
}

async function reloadDetail(): Promise<void> {
  detailCursor.value = null
  detailRows.value = []
  detailLoading.value = true
  detailError.value = ''
  try {
    detailPage.value = await ledgerSearch(TARGET, detailParams())
    detailRows.value = detailPage.value.items ?? []
    detailCursor.value = detailPage.value.next_cursor
  } catch (e: any) { detailError.value = String(e?.message ?? e) }
  finally { detailLoading.value = false }
}
async function loadMoreDetail(): Promise<void> {
  if (!detailCursor.value) return
  detailLoadingMore.value = true
  try {
    const next = await ledgerSearch(TARGET, detailParams(detailCursor.value))
    detailRows.value.push(...(next.items ?? []))
    detailCursor.value = next.next_cursor
  } catch (e: any) { detailError.value = String(e?.message ?? e) }
  finally { detailLoadingMore.value = false }
}
function detailParams(cursor?: string): Record<string, string> {
  const out: Record<string, string> = { target: TARGET, limit: '50' }
  if (detailBucket.value) out.bucket = detailBucket.value
  if (detailTable.value) out.q = detailTable.value
  else if (detailQ.value) out.q = detailQ.value
  if (cursor) out.cursor = cursor
  return out
}
function resetDetail(): void {
  detailBucket.value = ''; detailTable.value = ''; detailQ.value = ''
  reloadDetail()
}
async function openDetail(row: any): Promise<void> {
  try {
    const d = await ledgerDetail(TARGET, row.id)
    if (d?.kind === 'external_record') {
      extDetail.value = d
      extDetailTitle.value = `${d.doc_type_name || '文书详情'} · ${d.record_key}`
    }
  } catch (e: any) { toast.error(`详情加载失败：${e?.message ?? e}`) }
}

const TARGET = 'inf_cdc_frontend'
onMounted(() => {
  loadAll()
  if (tab.value === 'detail') reloadDetail()
  timer = window.setInterval(() => { if (autoRefresh.value) loadAll() }, 60_000)
})
onUnmounted(() => window.clearInterval(timer))
</script>

<script lang="ts">
export default { name: 'InfectiousMonitor' }
</script>

<style scoped>
.inf-page { display: flex; flex-direction: column; gap: 12px; }
.inf-header { display: flex; justify-content: space-between; align-items: center;
  gap: 12px; flex-wrap: wrap; }
.inf-heading { display: flex; align-items: center; gap: 12px; }
.inf-icon { width: 44px; height: 44px; border-radius: 10px; background: #ecfeff;
  color: #0e7490; display: grid; place-items: center; }
.inf-icon svg { width: 26px; height: 26px; }
.inf-eyebrow { font-size: 12px; color: #0e7490; margin: 0; }
.inf-header h2 { margin: 0; font-size: 20px; }
.inf-desc { font-size: 12px; color: var(--muted, #6b7280); margin: 2px 0 0; }
.inf-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.inf-badge { display: inline-flex; align-items: center; gap: 6px; font-size: 12px;
  border-radius: 999px; padding: 3px 10px; background: #f3f4f6; }
.inf-badge i { width: 8px; height: 8px; border-radius: 50%; background: #9ca3af; }
.inf-badge.ok { background: #ecfdf5; color: #047857; } .inf-badge.ok i { background: #059669; }
.inf-badge.warn { background: #fffbeb; color: #92400e; } .inf-badge.warn i { background: #d97706; }
.inf-badge.bad { background: #fef2f2; color: #b91c1c; } .inf-badge.bad i { background: #dc2626; }
.inf-badge.muted { color: #6b7280; }
.inf-sync, .inf-refresh { border: 1px solid var(--border, #e5e7eb); background: #fff;
  border-radius: 6px; padding: 5px 12px; font-size: 13px; cursor: pointer; }
.inf-sync { background: #0e7490; color: #fff; border-color: #0e7490; }
.inf-auto { font-size: 12px; color: var(--muted, #6b7280); }
.inf-notice { border-radius: 6px; padding: 6px 12px; font-size: 13px; }
.inf-notice.ok { background: #ecfdf5; color: #047857; }
.inf-notice.muted { background: #f3f4f6; color: #6b7280; }
.inf-notice.bad { background: #fef2f2; color: #b91c1c; }
.inf-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border, #e5e7eb); }
.inf-tabs button { border: none; background: none; padding: 8px 14px; font-size: 13px;
  cursor: pointer; color: var(--muted, #6b7280); border-bottom: 2px solid transparent; }
.inf-tabs button.active { color: #0e7490; border-bottom-color: #0e7490; font-weight: 600; }
.inf-pane { display: flex; flex-direction: column; gap: 12px; }
.inf-pane-note { font-size: 12px; }
.inf-metrics { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
@media (max-width: 1100px) { .inf-metrics { grid-template-columns: repeat(2, 1fr); } }
.inf-metric { text-align: left; border: 1px solid var(--border, #e5e7eb);
  border-radius: 10px; padding: 12px; background: var(--card, #fff); cursor: pointer;
  display: flex; flex-direction: column; gap: 4px; }
.inf-metric:hover { box-shadow: 0 1px 6px rgba(0,0,0,.08); }
.inf-metric .m-top { font-size: 12px; color: var(--muted, #6b7280); }
.inf-metric .m-val { font-size: 26px; font-weight: 700; }
.inf-metric .m-note { font-size: 11px; color: var(--muted, #9ca3af); }
.inf-metric.ok { border-top: 3px solid #059669; }
.inf-metric.wait { border-top: 3px solid #d97706; }
.inf-metric.bad { border-top: 3px solid #dc2626; }
.inf-metric.muted { border-top: 3px solid #9ca3af; }
.inf-metric.warn { border-top: 3px solid #f59e0b; }
.inf-columns { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; }
@media (max-width: 1000px) { .inf-columns { grid-template-columns: 1fr; } }
.inf-main, .inf-side { display: flex; flex-direction: column; gap: 12px; }
.inf-card { border: 1px solid var(--border, #e5e7eb); border-radius: 10px;
  padding: 12px; background: var(--card, #fff); display: flex;
  flex-direction: column; gap: 6px; }
.inf-card > b { font-size: 14px; }
.inf-muted { color: var(--muted, #6b7280); font-size: 12px; margin: 0; }
.inf-chart { display: flex; align-items: flex-end; gap: 6px; height: 150px; }
.inf-chart-day { flex: 1; display: flex; flex-direction: column; align-items: center;
  gap: 4px; }
.inf-chart-num { font-size: 11px; color: var(--muted, #6b7280); }
.inf-chart-bar-area { height: 110px; display: flex; align-items: flex-end; }
.inf-chart-bar { width: 26px; display: flex; flex-direction: column;
  justify-content: flex-end; background: #f3f4f6; border-radius: 3px; overflow: hidden; }
.inf-seg { display: block; width: 100%; }
.inf-seg.ok { background: #059669; } .inf-seg.wait { background: #d97706; }
.inf-seg.bad { background: #dc2626; } .inf-seg.unk { background: #b45309; }
.inf-chart-date { font-size: 11px; color: var(--muted, #9ca3af); }
.inf-legend { display: flex; gap: 14px; font-size: 12px; color: var(--muted, #6b7280); }
.inf-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  margin-right: 4px; }
.inf-legend .ok { background: #059669; } .inf-legend .wait { background: #d97706; }
.inf-legend .bad { background: #dc2626; } .inf-legend .unk { background: #b45309; }
.inf-mini-table { width: 100%; font-size: 12px; border-collapse: collapse; }
.inf-mini-table th, .inf-mini-table td { text-align: left; padding: 5px 8px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
.inf-attention { display: flex; align-items: center; gap: 10px; text-align: left;
  border: none; background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px;
  padding: 8px 10px; cursor: pointer; width: 100%; }
.inf-attention i { width: 8px; height: 8px; border-radius: 50%; background: #d97706; }
.inf-attention i.bad { background: #dc2626; }
.inf-attention strong { font-size: 13px; }
.inf-attention p { margin: 2px 0 0; font-size: 12px; color: var(--muted, #6b7280); }
.inf-facts { list-style: none; padding: 0; margin: 0; font-size: 12px;
  display: flex; flex-direction: column; gap: 4px; }
.inf-warn-tag { color: #b45309; font-weight: 700; }
.inf-dep { background: #f0f9ff; color: #0369a1; border-radius: 4px;
  padding: 1px 6px; font-size: 11px; }
.inf-code { color: #9ca3af; margin-left: 4px; }
.inf-lag { display: inline-block; width: 60px; height: 6px; border-radius: 3px;
  background: #f3f4f6; margin-right: 6px; vertical-align: middle; overflow: hidden; }
.inf-lag i { display: block; height: 100%; background: #059669; }
.inf-lag.warn i { background: #d97706; } .inf-lag.bad i { background: #dc2626; }
.inf-checks { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; }
@media (max-width: 1100px) { .inf-checks { grid-template-columns: repeat(3, 1fr); } }
.inf-check { border: 1px solid var(--border, #e5e7eb); border-radius: 8px;
  padding: 8px 10px; display: flex; flex-direction: column; gap: 2px; }
.inf-check.ok { border-top: 3px solid #059669; }
.inf-check.warn { border-top: 3px solid #d97706; }
.inf-check.bad { border-top: 3px solid #dc2626; }
.inf-check.muted { border-top: 3px solid #9ca3af; }
.ck-name { font-size: 12px; font-weight: 600; }
.ck-level { font-size: 11px; color: var(--muted, #6b7280); }
.ck-detail { font-size: 11px; color: var(--muted, #9ca3af); margin: 0;
  overflow: hidden; text-overflow: ellipsis; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.ck-ms { font-size: 11px; color: #cbd5e1; }
.inf-alerts { list-style: none; padding: 0; margin: 0; font-size: 12px;
  display: flex; flex-direction: column; gap: 6px; }
.inf-alert-time { color: var(--muted, #9ca3af); margin-right: 8px; }
.inf-daily { white-space: pre-wrap; font-size: 12px; background: var(--card, #fafafa);
  padding: 10px; border-radius: 6px; max-height: 420px; overflow: auto; }
.meta-line { font-size: 12px; }
.ext-facts { font-size: 12px; list-style: none; padding-left: 0; }
.diag { font-size: 13px; color: #92400e; background: #fffbeb;
  border: 1px solid #fde68a; border-radius: 6px; padding: 6px 10px; }
.ghost { border: 1px solid var(--border, #e5e7eb); background: #fff;
  border-radius: 6px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
</style>
