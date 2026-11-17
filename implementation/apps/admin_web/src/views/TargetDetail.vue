<!-- LT-06B P1 业务详情：概况（图）/台账/调度/告警/配置/外部导入（EXTERNAL）。
     药品：提示走专页（链接），仍展示台账摘要行。 -->
<template>
  <div v-if="target">
    <!-- PT-03：传染病业务提示直达专属监控座舱（/infectious 五页签） -->
    <div v-if="code === 'inf_cdc_frontend'" class="inf-jump">
      本业务已有专属监控座舱（运行总览 / 13 接口矩阵 / 守护体检 / 回执明细 / 对账）——
      <button class="ghost" @click="router.push('/infectious')">前往传染病上报专页</button>
    </div>
    <PageHeader :title="target.name" :subtitle="`${target.code} · ${target.authority}`">
      <template #actions>
        <button class="ghost" @click="router.back()">返回</button>
      </template>
    </PageHeader>

    <Tabs v-model="tab" :tabs="tabItems" sync-query>

      <!-- 概况 -->
      <div v-if="tab === 'overview'" class="tab-pane">
        <div v-if="target.plugin_key === 'drug_trace'" class="hint-bar">
          药品追溯明细请在专页查看
          <button @click="router.push('/drugtrace')">前往药品追溯专页</button>
        </div>
        <LoadingBlock v-if="sumLoading" />
        <ErrorState v-else-if="sumError" :message="sumError" @retry="loadSummary" />
        <EmptyState v-else-if="!summary || summary.data_source === 'none'"
                    text="暂无数据（该业务无摘要来源）" />
        <template v-else>
          <div class="stat-grid-sm">
            <StatCard v-for="b in bucketCards" :key="b.label" :label="b.label"
                      :value="b.value" :tone="b.tone" />
          </div>
          <p class="muted note-line">{{ summary.note }}</p>
          <div v-if="unmappedList.length" class="warn-line">
            存在未映射状态（{{ unmappedList.length }} 个）：
            <span v-for="(u, i) in unmappedList" :key="u.key">
              {{ i > 0 ? '；' : '' }}<code>{{ u.key }}</code> × {{ u.count }}</span>
          </div>
          <div class="two-col">
            <div class="card">
              <b>14 天趋势</b>
              <LineChart :series="trendSeries" :height="180" />
            </div>
            <div class="card">
              <b>状态分布</b>
              <Donut :parts="donutParts" caption="提交" />
            </div>
          </div>
        </template>
      </div>

      <!-- 台账 -->
      <div v-else-if="tab === 'ledger'" class="tab-pane">
        <LedgerPane v-if="code" :target="code" :can-resolve="canResolve" />
      </div>

      <!-- 调度 -->
      <div v-else-if="tab === 'schedules'" class="tab-pane">
        <LoadingBlock v-if="schedLoading" />
        <ErrorState v-else-if="schedError" :message="schedError" @retry="loadSchedules" />
        <EmptyState v-else-if="!schedules.length" text="该业务暂无调度登记" />
        <table v-else class="mini-table">
          <thead><tr><th>名称</th><th>cron</th><th>状态</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="s in schedules" :key="s.id">
              <td>{{ s.name }}</td>
              <td><code>{{ s.cron }}</code></td>
              <td>
                <StatusTag :status="s.observe_only ? 'OBSERVE_ONLY' : (s.enabled ? 'ENABLED' : 'DISABLED')" />
              </td>
              <td class="muted">{{ describeSchedule(s).label }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 告警 -->
      <div v-else-if="tab === 'alerts'" class="tab-pane">
        <LoadingBlock v-if="alertLoading" />
        <ErrorState v-else-if="alertError" :message="alertError" @retry="loadAlerts" />
        <EmptyState v-else-if="!alerts.length" text="该业务暂无告警" />
        <table v-else class="mini-table">
          <thead><tr><th>级别</th><th>内容</th><th>最近出现</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="a in alerts" :key="a.id">
              <td><StatusTag :status="String(a.severity).toUpperCase()" /></td>
              <td>{{ a.message }}</td>
              <td class="muted"><TimeText :value="a.last_seen_at" /></td>
              <td><StatusTag :status="a.state" /></td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 配置 -->
      <div v-else-if="tab === 'config'" class="tab-pane">
        <div class="two-col">
          <div class="card">
            <b>目录信息</b>
            <dl class="kv">
              <dt>生命周期</dt><dd>{{ target.lifecycle }}</dd>
              <dt>运行形态</dt><dd>{{ target.runtime_kind }}</dd>
              <dt>模式</dt><dd>{{ target.mode }}</dd>
              <dt>唯一推送方</dt>
              <dd>{{ target.sole_pusher }}
                <span v-if="target.sole_pusher_note" class="muted">（{{ target.sole_pusher_note }}）</span>
              </dd>
              <dt>插件</dt><dd>{{ target.plugin_key || '—' }}</dd>
              <dt>调度说明</dt><dd>{{ target.schedule_note || '—' }}</dd>
              <dt>README</dt><dd>{{ target.readme_path || '—' }}</dd>
            </dl>
            <b style="display:block;margin-top:10px">规范文件</b>
            <ul class="doc-list">
              <li v-for="d in target.spec_docs" :key="d">{{ d }}</li>
              <li v-if="!target.spec_docs?.length" class="muted">（无登记）</li>
            </ul>
            <p class="muted">文件路径仅展示，不提供在线下载。</p>
            <!-- RT-05 U02：写操作入口（角色与后端一致；服务端仍校验 scope/理由） -->
            <div v-if="canEditTarget" class="cfg-actions">
              <button class="ghost" @click="openEdit">编辑目录信息…</button>
              <button v-if="canUpgradeLifecycle" class="ghost"
                      @click="openLifecycle">生命周期变更…</button>
            </div>
            <p v-else class="muted small">当前角色无目录写权限（config_editor / platform_admin + 业务范围；前端隐藏不代表后端不校验）。</p>
          </div>
          <div class="card">
            <b>关联目的地</b>
            <EmptyState v-if="!destCfg" text="未绑定目的地" />
            <template v-else>
              <dl class="kv">
                <dt>适配器</dt><dd><code>{{ destCfg.profile_code || '—' }}</code></dd>
                <dt>Mock</dt>
                <dd>{{ destCfg.is_mock ? '是（仅本机回环）' : '否（真实，受闸门约束）' }}</dd>
                <dt v-if="destCfg.host_port">地址</dt>
                <dd v-if="destCfg.host_port"><code>{{ destCfg.host_port }}</code></dd>
                <dt v-if="destCfg.status">状态</dt>
                <dd v-if="destCfg.status"><StatusTag :status="destCfg.status" /></dd>
              </dl>
              <b style="display:block;margin:8px 0 4px">回执规则</b>
              <JsonViewer :data="destCfg.receipt_rules" />
            </template>
          </div>
        </div>
      </div>

      <!-- 联调台（medins_emr_sd） -->
      <div v-else-if="tab === 'debug'" class="tab-pane">
        <MedinsDebug />
      </div>

      <!-- 外部导入（EXTERNAL） -->
      <div v-else-if="tab === 'import'" class="tab-pane">
        <div class="hint-bar">
          只读导入 state 副本（本机文件）：result=true 只记"前置机受理"，不等于
          国家平台业务确认；dry-run 先行，正式导入需确认。
        </div>
        <ImportPane :target="code" />
      </div>
    </Tabs>

    <!-- RT-05 U02 编辑抽屉：初始草稿复制服务端对象；revision 乐观锁；
         409 保留输入并拉最新对照，不静默丢弃/不自动覆盖 -->
    <Drawer :open="editing" title="编辑目录信息" width="480px" @close="askCloseEdit">
      <label>名称</label>
      <input v-model.trim="editForm.name" />
      <label>类别</label>
      <input v-model.trim="editForm.category" />
      <label>主管单位</label>
      <input v-model.trim="editForm.authority" />
      <label>负责科室</label>
      <input v-model.trim="editForm.owner_dept" />
      <label>调度说明</label>
      <input v-model.trim="editForm.schedule_note" />
      <label>README 路径</label>
      <input v-model.trim="editForm.readme_path" />
      <label>唯一推送方说明 sole_pusher_note</label>
      <input v-model.trim="editForm.sole_pusher_note" />
      <p class="muted small">未展示字段（如 extra）按服务端现值原样保留，不因表单清空。</p>
      <p v-if="editError" class="form-error">
        {{ editError }}
        <button v-if="editConflict" class="ghost" @click="loadLatestForCompare">
          拉取最新数据对照</button>
      </p>
      <div v-if="latestCompare" class="compare-box">
        <b>服务端最新（对照，不覆盖你的输入）</b>
        <dl class="kv small-kv">
          <template v-for="k in Object.keys(latestCompare)" :key="k">
            <dt>{{ k }}</dt><dd>{{ latestCompare[k] ?? '—' }}</dd>
          </template>
        </dl>
      </div>
      <div class="drawer-actions">
        <button class="ghost" @click="askCloseEdit">取消</button>
        <button :disabled="editBusy" @click="submitEdit">
          {{ editBusy ? '保存中…' : '保存（revision 乐观锁）' }}</button>
      </div>
    </Drawer>

    <!-- 生命周期变更：升级必填理由（服务端同校验并审计）；
         修改目录不等于真实发送授权（闸门不动） -->
    <Drawer :open="lifecycling" title="生命周期变更" width="420px" @close="lifecycling = false">
      <label>当前生命周期</label>
      <p><StatusTag :status="target?.lifecycle" /></p>
      <label>目标生命周期 *</label>
      <select v-model="lifeTarget">
        <option value="">— 选择 —</option>
        <option v-for="l in LIFECYCLES" :key="l" :value="l">{{ l }}</option>
      </select>
      <label>理由 *（升级到 PILOT/PRODUCTION 必填 ≥5 字，进审计）</label>
      <textarea v-model="lifeReason" rows="3"
                placeholder="如：试点评审通过、已确认唯一推送方…"></textarea>
      <p class="muted small">修改目录 lifecycle 不等于真实发送授权：唯一推送方、
        独立启用证据与真实链路门禁不变。</p>
      <p v-if="lifeError" class="form-error">{{ lifeError }}</p>
      <div class="drawer-actions">
        <button class="ghost" @click="lifecycling = false">取消</button>
        <button :disabled="lifeBusy || !lifeTarget" @click="submitLifecycle">
          {{ lifeBusy ? '提交中…' : '提交变更' }}</button>
      </div>
    </Drawer>
  </div>
  <LoadingBlock v-else-if="loading" />
  <ErrorState v-else-if="error" :message="error" @retry="load" />
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getTarget, targetSummary, listSchedules, listAlerts,
  updateTarget } from '../api/index'
import type { AlertItem, ReportTarget, ScheduleItem, TargetSummary } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import Tabs from '../components/Tabs.vue'
import StatCard from '../components/StatCard.vue'
import StatusTag from '../components/StatusTag.vue'
import TimeText from '../components/TimeText.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import JsonViewer from '../components/JsonViewer.vue'
import LineChart from '../components/charts/LineChart.vue'
import Donut from '../components/charts/Donut.vue'
import { describeSchedule } from '../utils/dashboard'
import Drawer from '../components/Drawer.vue'
import { toast } from '../components/Toast'

const LedgerPane = defineAsyncComponent(() => import('./target/LedgerPane.vue'))
const ImportPane = defineAsyncComponent(() => import('./target/ImportPane.vue'))
const MedinsDebug = defineAsyncComponent(() => import('./target/MedinsDebug.vue'))

const route = useRoute()
const router = useRouter()

const code = computed(() => String(route.params.code ?? ''))
const tab = ref((route.query.tab as string) || 'overview')
const tabItems = computed(() => [
  { key: 'overview', label: '概况' },
  { key: 'ledger', label: '提交台账' },
  { key: 'schedules', label: '调度' },
  { key: 'alerts', label: '告警' },
  { key: 'config', label: '配置' },
  ...(target.value?.runtime_kind === 'EXTERNAL'
    ? [{ key: 'import', label: '外部导入' }] : []),
  ...(target.value?.code === 'medins_emr_sd'
    ? [{ key: 'debug', label: '联调台' }] : []),
])

const target = ref<ReportTarget | null>(null)
const loading = ref(true)
const error = ref('')
const summary = ref<TargetSummary | null>(null)
const sumLoading = ref(false)
const sumError = ref('')
const schedules = ref<ScheduleItem[]>([])
const schedLoading = ref(false)
const schedError = ref('')
const alerts = ref<AlertItem[]>([])
const alertLoading = ref(false)
const alertError = ref('')
// RT-01：目的地摘要直接读 get_target 附带的只读脱敏字段
// （后端 destination_summary），不再请求不存在的 /api/v1/destinations/{id}。
const destCfg = computed<any>(() => target.value?.destination_summary ?? null)

function roles(): string[] {
  try { return JSON.parse(localStorage.getItem('blyb.roles') || '[]') } catch { return [] }
}
const canResolve = computed(() => {
  const r = roles()
  return r.includes('platform_admin') || r.includes('business_reviewer')
})

// ---------------- RT-05 U02：目录编辑与生命周期 ----------------
const LIFECYCLES = ['PLANNED', 'DEVELOPING', 'PILOT', 'PRODUCTION', 'RETIRED']
const canEditTarget = computed(() => {
  const r = roles()
  return r.includes('platform_admin') || r.includes('config_editor')
})
const canUpgradeLifecycle = canEditTarget

const unmappedList = computed(() =>
  Object.entries(summary.value?.unmapped_states ?? {})
    .map(([key, count]) => ({ key, count: Number(count) })))

const editing = ref(false)
const editBusy = ref(false)
const editError = ref('')
const editConflict = ref(false)
const latestCompare = ref<Record<string, unknown> | null>(null)
const editForm = reactive<Record<string, string>>({
  name: '', category: '', authority: '', owner_dept: '',
  schedule_note: '', readme_path: '', sole_pusher_note: '' })

function openEdit(): void {
  // 初始草稿=服务端对象复制（含 revision）；未展示字段不进表单、保存时不动
  const t = target.value ?? {} as any
  editForm.name = t.name ?? ''; editForm.category = t.category ?? ''
  editForm.authority = t.authority ?? ''; editForm.owner_dept = t.owner_dept ?? ''
  editForm.schedule_note = t.schedule_note ?? ''
  editForm.readme_path = t.readme_path ?? ''
  editForm.sole_pusher_note = t.sole_pusher_note ?? ''
  editError.value = ''; editConflict.value = false; latestCompare.value = null
  editing.value = true
}
function askCloseEdit(): void {
  const t = target.value ?? {} as any
  const dirty = Object.keys(editForm).some(k => editForm[k] !== (t[k] ?? ''))
  if (dirty && !window.confirm('有未保存的修改，确认关闭并丢弃？')) return
  editing.value = false
}
async function loadLatestForCompare(): Promise<void> {
  try {
    const t = await getTarget(code.value)
    latestCompare.value = {
      name: t.name, category: t.category, authority: t.authority,
      owner_dept: t.owner_dept, schedule_note: t.schedule_note,
      readme_path: t.readme_path, sole_pusher_note: t.sole_pusher_note,
      revision: (t as any).revision }
  } catch (e: any) { editError.value = `拉取最新失败：${String(e?.message ?? e)}` }
}
async function submitEdit(): Promise<void> {
  editBusy.value = true; editError.value = ''; editConflict.value = false
  try {
    const t = await updateTarget(code.value, {
      ...editForm,
      revision: (target.value as any)?.revision,
    } as any)
    target.value = t
    toast.success('目录信息已保存')
    editing.value = false
  } catch (e: any) {
    if (e?.code === 'CONFLICT' || /revision mismatch/.test(String(e?.message))) {
      editConflict.value = true
      editError.value = '保存冲突（409）：他人已更新该业务。你的输入已保留；可拉取最新数据对照后再决定是否重新提交。'
    } else {
      editError.value = String(e?.message ?? e)   // 失败保留输入可重试
    }
  } finally { editBusy.value = false }
}

const lifecycling = ref(false)
const lifeBusy = ref(false)
const lifeError = ref('')
const lifeTarget = ref('')
const lifeReason = ref('')
function openLifecycle(): void {
  lifeTarget.value = ''; lifeReason.value = ''; lifeError.value = ''
  lifecycling.value = true
}
async function submitLifecycle(): Promise<void> {
  if (!lifeTarget.value) return
  lifeBusy.value = true; lifeError.value = ''
  try {
    target.value = await updateTarget(code.value, {
      lifecycle: lifeTarget.value,
      reason: lifeReason.value,
      revision: (target.value as any)?.revision,
    } as any)
    toast.success(`生命周期已变更为 ${lifeTarget.value}（审计已记录）`)
    lifecycling.value = false
  } catch (e: any) {
    lifeError.value = String(e?.message ?? e)   // 失败保留输入
  } finally { lifeBusy.value = false }
}

const bucketCards = computed(() => {
  const s = summary.value
  if (!s) return []
  const b = s.buckets ?? {}
  return [
    { label: '已确认', value: b.CONFIRMED ?? 0, tone: 'success' as const },
    { label: '被拒', value: b.REJECTED ?? 0, tone: 'danger' as const },
    { label: '未知', value: b.UNKNOWN ?? 0,
      tone: (b.UNKNOWN ?? 0) > 0 ? ('danger' as const) : ('info' as const) },
    { label: '积压（窗口）', value: s.backlog ?? 0, tone: 'warning' as const },
    { label: '积压（全量）',
      value: s.backlog_total ?? s.backlog ?? 0, tone: 'warning' as const },
    { label: '质量等待', value: s.quality_waiting ?? 0, tone: 'info' as const },
  ]
})

const trendSeries = computed(() => [{
  name: 'CONFIRMED', color: '#15803d',
  points: (summary.value?.daily ?? []).slice(-14).map(
    (r: any) => ({ x: String(r.date).slice(5), y: Number(r.CONFIRMED ?? 0) })),
}])

const DONUT_COLORS: Record<string, string> = {
  CONFIRMED: '#15803d', ACCEPTED: '#b45309', PARTIAL: '#a16207',
  REJECTED: '#b91c1c', UNKNOWN: '#92400e', PENDING: '#0e7490',
  SENDING: '#0891b2', NOT_REPORTABLE: '#6b7280' }
const donutParts = computed(() => {
  const b = summary.value?.buckets ?? {}
  return Object.entries(b).filter(([, v]) => Number(v) > 0).map(([k, v]) => ({
    name: k, color: DONUT_COLORS[k] ?? '#9ca3af', value: Number(v) }))
})

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    target.value = await getTarget(code.value)
    loadSummary()
    loadSchedules()
    loadAlerts()
  } catch (e: any) {
    error.value = String(e?.message ?? e)
  } finally {
    loading.value = false
  }
}

async function loadSummary(): Promise<void> {
  sumLoading.value = true
  sumError.value = ''
  try {
    summary.value = await targetSummary(code.value, 7)
  } catch (e: any) {
    sumError.value = String(e?.message ?? e)
  } finally {
    sumLoading.value = false
  }
}

async function loadSchedules(): Promise<void> {
  schedLoading.value = true
  schedError.value = ''
  try {
    schedules.value = await listSchedules(code.value)
  } catch (e: any) {
    schedError.value = String(e?.message ?? e)
  } finally {
    schedLoading.value = false
  }
}

async function loadAlerts(): Promise<void> {
  alertLoading.value = true
  alertError.value = ''
  try {
    // RT-03 S08：alerts2 返回分页对象（首页 50 条，该页不做加载更多）
    const resp = await listAlerts({ target: code.value, limit: 50 })
    alerts.value = resp.items
  } catch (e: any) {
    alertError.value = String(e?.message ?? e)
  } finally {
    alertLoading.value = false
  }
}

watch(() => route.params.code, () => route.params.code && load())
onMounted(load)
</script>

<style scoped>
.tab-pane { padding-top: 12px; }
.stat-grid-sm { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; margin-bottom: 10px; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
@media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }
.kv { display: grid; grid-template-columns: 100px 1fr; gap: 4px 10px; font-size: 13px; margin: 8px 0; }
.kv dt { color: var(--muted, #6b7280); }
.kv dd { margin: 0; word-break: break-all; }
.doc-list { font-size: 12px; word-break: break-all; padding-left: 18px; }
.hint-bar { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af;
  padding: 8px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 12px;
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.cfg-actions { display: flex; gap: 8px; margin-top: 10px; }
.form-error { color: var(--c-danger, #b91c1c); font-size: 12px; margin: 8px 0 0; }
.drawer-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.compare-box { margin-top: 10px; border: 1px dashed var(--border, #e5e7eb); border-radius: 6px; padding: 8px; }
.small-kv { font-size: 12px; }
.warn-line { background: #fffbeb; border: 1px solid #fde68a; color: #92400e;
  padding: 6px 10px; border-radius: 6px; font-size: 12px; }
.note-line { font-size: 12px; margin: 6px 0; }
.mini-table { width: 100%; font-size: 13px; border-collapse: collapse; }
.mini-table th, .mini-table td { text-align: left; padding: 6px 8px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
.inf-jump { background: #ecfeff; color: #0e7490; border: 1px solid #a5f3fc;
  border-radius: 8px; padding: 8px 14px; font-size: 13px; display: flex;
  align-items: center; gap: 10px; margin-bottom: 10px; }
</style>
