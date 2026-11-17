<!-- LT-06B P1 上报驾驶舱（新首页）：待办条 + 业务卡片网格 + 趋势 + 晨报/最近运行。
     单业务 summary 报错不影响其他卡（overview 后端已隔离 error 字段，卡片局部 ErrorState）。
     RT-01：① 卡片直接吃 TargetSummary（target_code 字段，排序兼容不再白屏）；
     ② 积压展示修正为窗口/全量双口径（不再误读不存在的 backlogTotal）；
     ③ 受限账号晨报 403 温和降级；④ 时间统一 TimeText（院内东八区）。 -->
<template>
  <div>
    <PageHeader title="上报驾驶舱" :subtitle="`数据时间 ${loadedAt || '…'}`">
      <template #actions>
        <select v-model="days" @change="load">
          <option :value="1">今天</option>
          <option :value="7">近 7 天</option>
          <option :value="30">近 30 天</option>
        </select>
        <button class="ghost" @click="load">刷新</button>
        <label class="auto-refresh">
          <input type="checkbox" v-model="autoRefresh" /> 自动刷新（60s）
        </label>
      </template>
    </PageHeader>

    <div v-if="todos.length" class="todo-bar">
      <div v-for="t in todos" :key="t.key" class="todo-item" :class="t.tone"
           @click="t.to && router.push(t.to)">
        {{ t.text }}
      </div>
    </div>

    <LoadingBlock v-if="loading && !overview" />
    <ErrorState v-else-if="error" :message="error" @retry="load" />
    <template v-else-if="overview">
      <div class="card-grid">
        <div v-for="t in sortedCards" :key="t.target_code"
             class="target-card" :class="{ 'data-none': t.data_source === 'none' }"
             @click="router.push(`/targets/${encodeURIComponent(t.target_code)}`)">
          <div class="tc-head">
            <b>{{ t.name || t.target_code }}</b>
            <StatusTag :status="t.lifecycle || 'UNKNOWN'" />
          </div>
          <div class="tc-sub">
            {{ runtimeLabel(t.runtime_kind) }}
            <span v-if="t.evidence_kind === 'mock'" class="mock-badge">Mock 证据</span>
          </div>
          <template v-if="t.error">
            <div class="tc-error">摘要不可用（{{ t.error }}）</div>
          </template>
          <template v-else-if="t.data_source === 'none'">
            <div class="tc-none">暂无数据</div>
          </template>
          <template v-else>
            <div class="tc-main">
              <div class="tc-num">
                <span class="big">{{ primaryMetric(t).value }}</span>
                <span class="lbl">{{ primaryMetric(t).label }}</span>
                <span v-if="primaryMetric(t).badge" class="accepted-badge">{{ primaryMetric(t).badge }}</span>
              </div>
              <div class="tc-nums">
                <span>被拒 {{ t.buckets?.REJECTED ?? 0 }}</span>
                <span :class="{ 'num-danger': (t.buckets?.UNKNOWN ?? 0) > 0 }">
                  未知 {{ t.buckets?.UNKNOWN ?? 0 }}</span>
                <span>积压（窗口）{{ t.backlog ?? 0 }}</span>
                <span v-if="t.backlog_total != null">积压（全量）{{ t.backlog_total }}</span>
              </div>
            </div>
            <div v-if="t.quality_waiting" class="tc-wait">
              质量等待 {{ t.quality_waiting }}（无提交）
            </div>
            <div class="tc-foot">
              <span>{{ t.last_success_at ? '最近确认' : '无业务确认记录（受理见上）' }}<TimeText
                v-if="t.last_success_at" :value="t.last_success_at" /></span>
              <span class="src-badge" :class="t.data_source">
                {{ t.data_source === 'external_ledger' ? '导入副本' : '平台' }}
              </span>
            </div>
          </template>
        </div>
      </div>

      <div class="dash-bottom">
        <div class="card">
          <b>趋势（{{ days === 1 ? '当天' : `近 ${days} 天` }}合计）</b>
          <p class="muted">院内自然日汇总；已确认/已受理/被拒/未知 堆叠</p>
          <BarChart :cats="trendCats" :height="200" stacked />
        </div>
        <div class="card">
          <b>今日晨报</b>
          <LoadingBlock v-if="digestLoading" />
          <ErrorState v-else-if="digestError" :message="digestError" small
                      @retry="loadDigest" />
          <p v-else-if="digestNotice" class="muted digest-note">{{ digestNotice }}</p>
          <EmptyState v-else-if="!digest?.text" text="暂无晨报（调度默认停用）" />
          <details v-else>
            <summary>生成于 <TimeText :value="digest.sent_at" /></summary>
            <pre class="digest-pre">{{ digest.text }}</pre>
          </details>
        </div>
        <div class="card">
          <b>最近调度运行</b>
          <EmptyState v-if="!recentRuns.length" text="暂无运行记录" />
          <ul v-else class="run-list">
            <li v-for="r in recentRuns" :key="r.id">
              <StatusTag :status="r.state" />
              <span class="run-key">{{ r.run_key.slice(0, 24) }}</span>
              <span class="muted"><TimeText v-if="r.ended_at" :value="r.ended_at" /><template v-else>进行中</template></span>
            </li>
          </ul>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { centerOverview, listAlerts, latestDigest, listSchedules, scheduleRuns } from '../api/index'
import type { ScheduleItem, ScheduleRun, TargetSummary } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import TimeText from '../components/TimeText.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import BarChart from '../components/charts/BarChart.vue'
import { buildTodos, sortTargets, stackDaily, primarySuccessMetric }
  from '../utils/dashboard'
import { formatHospitalTime } from '../utils/time'
import type { Todo } from '../utils/dashboard'

// PT-03：驾驶舱主数字口径（已确认/已受理自适应 + 前置机受理角标）
function primaryMetric(t: any): { value: number, label: string, badge: string } {
  return primarySuccessMetric(t) as any
}

const router = useRouter()
const days = ref(7)
const loading = ref(false)
const error = ref('')
const overview = ref<{ targets: TargetSummary[] } | null>(null)
const alerts = ref<any[]>([])
const alertsError = ref('')
const digest = ref<{ text: string | null, sent_at: string | null } | null>(null)
const digestLoading = ref(false)
const digestError = ref('')
const digestNotice = ref('')
const schedules = ref<ScheduleItem[]>([])
const runsBySched = ref<Record<string, ScheduleRun[]>>({})
const loadedAt = ref('')
const autoRefresh = ref(false)
let timer: number | undefined

const sortedCards = computed(() => sortTargets(
  (overview.value?.targets ?? []).map(t => ({
    ...t, lifecycle: t.lifecycle ?? 'PLANNED' })) as any[]))

const todos = computed<Todo[]>(() => buildTodos(
  alerts.value,
  overview.value?.targets ?? [],
  (overview.value?.targets ?? []).map(t => ({
    code: t.target_code, name: t.name ?? t.target_code,
    sole_pusher: t.sole_pusher ?? 'UNCONFIRMED' }))))

// PT-03：趋势四序列（补"已受理"——前置机受理型业务不再贴地为 0）
const trendCats = computed(() => stackDaily(overview.value?.targets ?? [])
  .slice(-14).map(row => ({
    label: row.date.slice(5),
    values: [
      { name: '已确认', color: '#15803d', value: row.CONFIRMED ?? 0 },
      { name: '已受理', color: '#0e7490', value: row.ACCEPTED ?? 0 },
      { name: '被拒', color: '#b91c1c', value: row.REJECTED ?? 0 },
      { name: '未知', color: '#b45309', value: row.UNKNOWN ?? 0 },
    ] })))

const recentRuns = computed<ScheduleRun[]>(() =>
  Object.values(runsBySched.value).flat()
    .sort((a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? ''))
    .slice(0, 10))

function runtimeLabel(kind?: string): string {
  if (kind === 'EXTERNAL') return '外部程序执行'
  if (kind === 'MANUAL') return '人工执行'
  if (kind === 'PLATFORM') return '平台执行'
  return ''
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [ov, al] = await Promise.allSettled([
      centerOverview(days.value),
      listAlerts({ state: 'OPEN', limit: 20 }),
    ])
    if (ov.status === 'fulfilled') {
      overview.value = ov.value as any
      loadedAt.value = formatHospitalTime(new Date())
    } else {
      error.value = String(ov.reason ?? '总览加载失败')
    }
    // RT-03 S08：alerts2 返回分页对象（items/next_cursor）——驾驶舱只取首页
    alerts.value = al.status === 'fulfilled' ? al.value.items : []
    alertsError.value = al.status === 'rejected' ? String(al.reason) : ''
    loadSchedules()
    loadDigest()
  } finally {
    loading.value = false
  }
}

async function loadSchedules(): Promise<void> {
  try {
    schedules.value = await listSchedules()
    const recent = schedules.value.filter(s => s.enabled || s.last_fire_at)
      .slice(0, 3)
    const out: Record<string, ScheduleRun[]> = {}
    for (const s of recent) {
      try {
        out[s.id] = (await scheduleRuns(s.id, 5)).items
      } catch { /* 单调度运行记录失败不阻断整卡 */ }
    }
    runsBySched.value = out
  } catch { /* 调度区失败不阻断驾驶舱主体 */ }
}

async function loadDigest(): Promise<void> {
  digestLoading.value = true
  digestError.value = ''
  digestNotice.value = ''
  try {
    digest.value = await latestDigest()
  } catch (e: any) {
    // RT-01：全院晨报对受限业务范围账号 403——温和说明，不显示红色异常框
    if (e?.status === 403) {
      digestNotice.value = '全院晨报仅对全院范围账号开放'
    } else {
      digestError.value = String(e?.message ?? e)
    }
  } finally {
    digestLoading.value = false
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(() => {
    if (autoRefresh.value) load()
  }, 60_000)
})
onUnmounted(() => window.clearInterval(timer))
</script>

<style scoped>
.todo-bar { display: flex; flex-direction: column; gap: 6px; margin: 8px 0 16px; }
.todo-item { padding: 6px 12px; border-radius: 6px; font-size: 13px; cursor: default; }
.todo-item.danger { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }
.todo-item.warning { background: #fffbeb; border: 1px solid #fde68a; color: #92400e; }
.todo-item:hover { filter: brightness(0.98); }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(270px, 1fr)); gap: 12px; }
.target-card { border: 1px solid var(--border, #e5e7eb); border-radius: 8px; padding: 12px; cursor: pointer; background: var(--card, #fff); }
.target-card:hover { box-shadow: 0 1px 4px rgba(0,0,0,.08); }
.target-card.data-none { opacity: .6; }
.tc-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.tc-sub { color: var(--muted, #6b7280); font-size: 12px; margin: 2px 0 8px; }
.mock-badge { background: #f3e8ff; color: #7e22ce; border-radius: 4px; padding: 0 6px; font-size: 11px; margin-left: 6px; }
.tc-main { display: flex; align-items: baseline; gap: 12px; }
.tc-num .big { font-size: 26px; font-weight: 700; }
.tc-num .lbl { color: var(--muted, #6b7280); margin-left: 4px; font-size: 12px; }
.tc-nums { display: flex; flex-direction: column; font-size: 12px; color: var(--muted, #6b7280); gap: 2px; }
.num-danger { color: #b91c1c; font-weight: 700; }
.tc-wait { font-size: 12px; color: #92400e; margin-top: 6px; }
.tc-foot { display: flex; justify-content: space-between; margin-top: 8px; font-size: 12px; color: var(--muted, #6b7280); }
.src-badge { border-radius: 4px; padding: 0 6px; font-size: 11px; }
.src-badge.platform { background: #eff6ff; color: #1d4ed8; }
.src-badge.external_ledger { background: #f0fdf4; color: #15803d; }
.tc-error { color: #b91c1c; font-size: 12px; padding: 8px 0; }
.tc-none { color: var(--muted, #9ca3af); padding: 12px 0; }
.dash-bottom { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 12px; margin-top: 16px; }
@media (max-width: 960px) { .dash-bottom { grid-template-columns: 1fr; } }
.digest-pre { white-space: pre-wrap; font-size: 12px; max-height: 260px; overflow: auto; }
.digest-note { font-size: 12px; margin: 8px 0; }
.run-list { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-direction: column; gap: 6px; }
.run-list li { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.run-key { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.auto-refresh { font-size: 12px; color: var(--muted, #6b7280); }
.accepted-badge { background: #ecfeff; color: #0e7490; border-radius: 4px;
  padding: 0 6px; font-size: 11px; margin-left: 6px; }
</style>
