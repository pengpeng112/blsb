<!-- LT-06B P2 调度任务：列表（cron 按需预览/启停需理由）+ 详情 Drawer（运行记录/立即运行）。
     镜像调度：只显示标签与说明，无任何启用/运行能力（后端同样拒绝）。
     RT-01：① 去掉挂载时逐行发 cronPreview 的 N+1 请求，改为点击"预览"按需
     请求并按 cron 表达式缓存；② window.alert 全部换 ConfirmDialog(理由) + toast；
     ③ 时间统一 TimeText（院内东八区）。 -->
<template>
  <div>
    <PageHeader title="调度任务" subtitle="平台内调度（镜像仅观测；生产外部调度的切换须用户在场）">
      <template #actions>
        <button v-if="isAdmin" @click="openCreate">新建调度…</button>
      </template>
    </PageHeader>
    <DataTable :columns="cols" :rows="rows" :loading="loading" :error="error"
               @retry="load" @row-click="openDetail" empty-text="暂无调度">
      <template #state="{ row }">
        <StatusTag :status="row.observe_only ? 'OBSERVE_ONLY'
          : (row.enabled ? 'ENABLED' : 'DISABLED')" />
      </template>
      <template #cron="{ row }">
        <span class="cron-cell">
          <code>{{ row.cron }}</code>
          <button class="ghost mini" @click.stop="togglePreview(row.cron)">
            {{ previewOpen === row.cron ? '收起' : '预览' }}</button>
        </span>
        <div v-if="previewOpen === row.cron" class="preview-box" @click.stop>
          <span v-if="previews[row.cron]?.loading" class="muted">计算中…</span>
          <span v-else-if="previews[row.cron]?.error" class="preview-err">
            预览失败：{{ previews[row.cron].error }}</span>
          <template v-else>
            <span class="muted">未来触发（院内时间）：</span>
            <ul>
              <li v-for="n in previews[row.cron]?.next ?? []" :key="n">
                <TimeText :value="n" /></li>
            </ul>
          </template>
        </div>
      </template>
      <template #next="{ row }">{{ describeSchedule(row).label }}</template>
    </DataTable>

    <Drawer :open="!!current" :title="current?.name ?? ''" @close="current = null">
      <template v-if="current">
        <div class="kv">
          <span class="muted">编码</span><code>{{ current.code }}</code>
          <span class="muted">job 类型</span><code>{{ current.job_type }}</code>
          <span class="muted">并发策略</span><span>{{ current.concurrency }}</span>
          <span class="muted">错过策略</span><span>{{ current.misfire_policy }}</span>
          <span class="muted">维护窗口</span>
          <span>{{ current.blackout?.length ?
            current.blackout.map((b: any) => `${b.start}–${b.end}`).join('、') : '无' }}</span>
          <span class="muted">上次触发</span><TimeText :value="current.last_fire_at" />
          <span class="muted">说明</span><span>{{ current.note || '—' }}</span>
        </div>
        <div v-if="current.observe_only" class="mirror-bar">
          镜像调度仅观测：生产由外部定时执行。启用/立即运行被服务端拒绝；
          修改说明不构成接管授权。
        </div>
        <div v-else class="actions">
          <button v-if="!current.enabled" class="ghost"
                  @click="askToggle(current, true)">启用…</button>
          <button v-else class="ghost" @click="askToggle(current, false)">停用…</button>
          <button @click="askRunNow(current)">立即运行…</button>
          <button v-if="isAdmin" class="ghost" @click="openEdit(current)">编辑…</button>
        </div>
        <h4>运行记录</h4>
        <LoadingBlock v-if="runsLoading" />
        <ErrorState v-else-if="runsError && !runs.length" :message="runsError" compact
                    @retry="reloadRuns" />
        <EmptyState v-else-if="!runs.length" text="暂无运行记录" />
        <template v-else>
          <table class="mini-table">
            <thead><tr><th>触发</th><th>开始</th><th>状态</th><th>耗时</th><th>迟到</th><th>错误</th></tr></thead>
            <tbody>
              <tr v-for="r in runs" :key="r.id" :title="runStateHint(r.state)">
                <td>{{ r.run_key.slice(0, 22) }}</td>
                <td><TimeText :value="r.started_at ?? r.fire_time" /></td>
                <td>
                  <StatusTag :status="r.state" />
                  <span v-if="runStateHint(r.state)" class="muted small">
                    {{ runStateHint(r.state) }}</span>
                </td>
                <td>{{ durationLabel(r.duration_seconds) }}</td>
                <td>{{ r.result_summary?.late ? '是' : '' }}</td>
                <td class="muted">{{ r.error_message || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="runsError" class="load-more-err">
            加载更多失败：{{ runsError }}
            <button class="ghost mini" @click="loadMoreRuns">重试</button>
          </p>
          <div v-else-if="runsHasMore" class="load-more">
            <button class="ghost" :disabled="runsLoadingMore"
                    @click="loadMoreRuns">
              {{ runsLoadingMore ? '加载中…' : '加载更多' }}</button>
          </div>
          <p v-else-if="runs.length" class="muted small">已加载全部运行记录</p>
        </template>
      </template>
    </Drawer>

    <!-- RT-05 U03 编辑抽屉：新建默认停用（启用走专用端点+理由审计）；
         cron 300ms 防抖预览（请求序号防旧响应覆盖）；blackout 行编辑；
         payload JSON 非法阻止提交；编辑带 revision，409 不覆盖 -->
    <Drawer :open="formOpen" :title="formMode === 'create' ? '新建调度（默认停用）' : `编辑调度 ${form.code}`"
            width="540px" @close="askCloseForm">
      <label>编码 code *（创建后不可改）</label>
      <input v-model.trim="form.code" :disabled="formMode === 'edit'" />
      <label>名称 *</label>
      <input v-model.trim="form.name" />
      <label>业务 target_code</label>
      <select v-model="form.target_code">
        <option value="">（平台全局，无业务绑定）</option>
        <option v-for="t in targets" :key="t.code" :value="t.code">
          {{ t.code }}（{{ t.name }}）</option>
      </select>
      <label>job 类型 *（仅真实已注册类型）</label>
      <select v-model="form.job_type">
        <option value="">— 选择 —</option>
        <option v-for="j in meta.job_types" :key="j" :value="j">{{ j }}</option>
      </select>
      <p v-if="!meta.job_types.length" class="muted small">
        job 类型元数据加载失败：不展示未注册类型，请重试。</p>
      <label>cron *</label>
      <input v-model.trim="form.cron" placeholder="如 30 8,11,14,17,20 * * *" />
      <div class="preview-box form-preview">
        <span v-if="formCronPreview.loading" class="muted">预览计算中…</span>
        <span v-else-if="formCronPreview.error" class="preview-err">
          {{ formCronPreview.error }}（修正前不可保存）</span>
        <template v-else-if="formCronPreview.next.length">
          <span class="muted">未来触发（院内时间）：</span>
          <ul><li v-for="n in formCronPreview.next" :key="n"><TimeText :value="n" /></li></ul>
        </template>
      </div>
      <div class="form-row">
        <span>
          <label>错过策略</label>
          <select v-model="form.misfire_policy">
            <option v-for="m in meta.misfire_policies" :key="m" :value="m">{{ m }}</option>
          </select>
        </span>
        <span>
          <label>并发策略</label>
          <select v-model="form.concurrency">
            <option v-for="c in meta.concurrency_modes" :key="c" :value="c">{{ c }}</option>
          </select>
        </span>
        <span>
          <label>最长运行秒数</label>
          <input v-model.number="form.max_runtime_seconds" type="number" min="1" />
        </span>
      </div>
      <label>维护窗口 blackout（HH:MM–HH:MM，可跨午夜：end&lt;start 表示跨天）</label>
      <div v-for="(b, i) in form.blackout" :key="i" class="blackout-row">
        <input v-model.trim="b.start" placeholder="08:30" maxlength="5" />
        <span>–</span>
        <input v-model.trim="b.end" placeholder="07:00" maxlength="5" />
        <button class="ghost mini" @click="form.blackout.splice(i, 1)">删除</button>
      </div>
      <button class="ghost mini" @click="form.blackout.push({ start: '', end: '' })">
        + 加一行</button>
      <label>payload 模板（JSON；非法阻止提交）</label>
      <textarea v-model="form.payload_template" rows="4"
                placeholder='{"key": "value"}' spellcheck="false"></textarea>
      <p v-if="payloadError" class="form-error">{{ payloadError }}</p>
      <label>说明 note</label>
      <input v-model.trim="form.note" />
      <p class="muted small">新建默认停用；启用必须走"启用…"专用端点并填写理由
        （PUT 不夹带 enabled，绕过审计的路不存在）。</p>
      <p v-if="formError" class="form-error">
        {{ formError }}
        <button v-if="formConflict" class="ghost mini" @click="reloadForConflict">
          拉取最新对照</button>
      </p>
      <div class="drawer-actions">
        <button class="ghost" @click="askCloseForm">取消</button>
        <button :disabled="formBusy || !canSubmitForm" @click="submitForm">
          {{ formBusy ? '提交中…' : (formMode === 'create' ? '创建（停用态）' : '保存修改') }}</button>
      </div>
    </Drawer>

    <ConfirmDialog :open="confirm.open" :title="confirm.title"
                   :confirm-text="confirm.confirmText" :busy="confirm.busy"
                   require-reason v-model:reason="confirm.reason"
                   @update:open="confirm.open = $event" @confirm="onConfirm">
      {{ confirm.hint }}
    </ConfirmDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { listSchedules, cronPreview, enableSchedule, disableSchedule, runNow,
  scheduleRuns, schedulesMeta, createSchedule, updateSchedule } from '../api/index'
import { listTargets } from '../api/index'
import type { ScheduleItem, ScheduleRun } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import StatusTag from '../components/StatusTag.vue'
import TimeText from '../components/TimeText.vue'
import Drawer from '../components/Drawer.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import { toast } from '../components/Toast'
import { describeSchedule } from '../utils/dashboard'
import { durationLabel, mergePagedItems, runStateHint } from '../utils/paging'

const rows = ref<ScheduleItem[]>([])
const loading = ref(true)
const error = ref('')
const current = ref<ScheduleItem | null>(null)
const runs = ref<ScheduleRun[]>([])
const runsCursor = ref<string | null>(null)     // RT-03 S08：真实 keyset 游标
const runsLoading = ref(false)
const runsLoadingMore = ref(false)
const runsError = ref('')
const runsHasMore = computed(() => runsCursor.value != null)

// cron 预览：按需请求 + 按表达式缓存（同一 cron 多行共用一次请求）
interface Preview { loading: boolean, error: string, next: string[] }
const previews = ref<Record<string, Preview>>({})
const previewOpen = ref('')

type Kind = 'enable' | 'disable' | 'run'
const confirm = ref({ open: false, busy: false, reason: '', title: '',
  hint: '', confirmText: '确认', kind: 'enable' as Kind,
  target: null as ScheduleItem | null })

const cols = [
  { key: 'name', title: '名称' },
  { key: 'code', title: '编码', width: '170px' },
  { key: 'target_code', title: '业务', width: '150px' },
  { key: 'cron', title: 'cron', width: '200px', slot: 'cron' as const },
  { key: 'state', title: '状态', width: '110px', slot: 'state' as const },
  { key: 'next', title: '下次触发', slot: 'next' as const },
  { key: 'concurrency', title: '并发', width: '80px' },
]

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    rows.value = await listSchedules()
  } catch (e: any) {
    error.value = String(e?.message ?? e)
  } finally {
    loading.value = false
  }
}

async function togglePreview(cron: string): Promise<void> {
  if (previewOpen.value === cron) { previewOpen.value = ''; return }
  previewOpen.value = cron
  const cached = previews.value[cron]
  if (cached && !cached.error) return          // 命中缓存（失败的允许重试）
  previews.value[cron] = { loading: true, error: '', next: [] }
  try {
    const p = await cronPreview(cron, 5)
    previews.value[cron] = { loading: false, error: '', next: p.next ?? [] }
  } catch (e: any) {
    previews.value[cron] = { loading: false, error: String(e?.message ?? e), next: [] }
  }
}

async function openDetail(row: ScheduleItem): Promise<void> {
  current.value = row
  await reloadRuns()
}
/** 首页加载：重置游标与旧内容（换调度必清旧游标，防串页）。 */
async function reloadRuns(): Promise<void> {
  if (!current.value) return
  runs.value = []
  runsCursor.value = null
  runsError.value = ''
  runsLoading.value = true
  try {
    const resp = await scheduleRuns(current.value.id, 20)
    runs.value = resp.items
    runsCursor.value = resp.next_cursor ?? null
  } catch (e: any) { runsError.value = String(e?.message ?? e) }
  finally { runsLoading.value = false }
}
/** 加载更多：失败保留已加载内容与游标（可重试）；防重复点击并发。 */
async function loadMoreRuns(): Promise<void> {
  if (!current.value || !runsCursor.value || runsLoadingMore.value) return
  runsLoadingMore.value = true
  runsError.value = ''
  try {
    const resp = await scheduleRuns(current.value.id, 20, runsCursor.value)
    runs.value = mergePagedItems(runs.value, resp.items)
    runsCursor.value = resp.next_cursor ?? null
  } catch (e: any) { runsError.value = String(e?.message ?? e) }
  finally { runsLoadingMore.value = false }
}

function askToggle(s: ScheduleItem, enable: boolean): void {
  confirm.value = {
    open: true, busy: false, reason: '', target: s,
    kind: enable ? 'enable' : 'disable',
    title: enable ? `启用 ${s.name}` : `停用 ${s.name}`,
    confirmText: enable ? '确认启用' : '确认停用',
    hint: enable
      ? '启用后按 cron 触发（系统任务会写库/写文件并占用 worker）。'
      : '停用后不再触发（运行中的任务不受影响）。',
  }
}
function askRunNow(s: ScheduleItem): void {
  confirm.value = {
    open: true, busy: false, reason: '', target: s, kind: 'run',
    title: `立即运行 ${s.name}`, confirmText: '确认运行',
    hint: '手动触发遵守并发策略与维护窗口（本批不提供绕过）。',
  }
}

async function onConfirm(reason: string): Promise<void> {
  const c = confirm.value
  const s = c.target
  if (!s) return
  c.busy = true
  try {
    if (c.kind === 'enable') {
      await enableSchedule(s.id, reason)
      toast.success(`已启用 ${s.name}`)
    } else if (c.kind === 'disable') {
      await disableSchedule(s.id, reason)
      toast.success(`已停用 ${s.name}`)
    } else {
      const r = await runNow(s.id, reason)
      toast.success(`已入队（run ${String(r.run_key).slice(0, 24)}）`)
    }
    c.open = false
    await load()
    // 列表刷新后让抽屉显示最新对象与运行记录
    if (current.value) {
      current.value = rows.value.find(x => x.id === current.value?.id) ?? current.value
      reloadRuns()
    }
  } catch (e: any) {
    toast.error(`操作被拒绝：${e?.message ?? e}`)
  } finally {
    c.busy = false
  }
}
// ---------------- RT-05 U03：新建/编辑 ----------------
const isAdmin = computed(() => {
  let roles: string[] = []
  try { roles = JSON.parse(localStorage.getItem('blyb.roles') || '[]') } catch { roles = [] }
  return roles.includes('platform_admin')
})

interface SchedMeta { job_types: string[], misfire_policies: string[],
  concurrency_modes: string[] }
const meta = ref<SchedMeta>({ job_types: [], misfire_policies: [], concurrency_modes: [] })
const targets = ref<{ code: string, name: string }[]>([])

const formOpen = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const formBusy = ref(false)
const formError = ref('')
const formConflict = ref(false)
const editTarget = ref<ScheduleItem | null>(null)
const form = reactive({
  code: '', name: '', target_code: '', job_type: '', cron: '',
  misfire_policy: 'SKIP', concurrency: 'FORBID',
  max_runtime_seconds: 3600 as number | string,
  blackout: [] as { start: string, end: string }[],
  payload_template: '', note: '' })

const payloadError = computed(() => {
  const t = form.payload_template.trim()
  if (!t) return ''                        // 空=模板（明示规则）
  try {
    const v = JSON.parse(t)
    if (v === null || typeof v !== 'object' || Array.isArray(v)) {
      return 'payload 模板须为 JSON 对象'
    }
    return ''
  } catch (e: any) { return `payload JSON 非法：${e.message}（修正前不可提交）` }
})
const cronInvalid = computed(() => /修正前不可保存/.test(formCronPreview.value.error))
const canSubmitForm = computed(() =>
  form.code && form.name && form.job_type && form.cron &&
  !payloadError.value && !cronInvalid.value)

/** cron 300ms 防抖预览；请求序号防旧响应覆盖新表达式；卸载清理定时器。 */
const formCronPreview = ref<{ loading: boolean, error: string, next: string[] }>(
  { loading: false, error: '', next: [] })
let cronTimer: ReturnType<typeof setTimeout> | null = null
let cronSeq = 0
watch(() => form.cron, (expr) => {
  cronSeq++
  const seq = cronSeq
  if (cronTimer) clearTimeout(cronTimer)
  if (!expr.trim()) {
    formCronPreview.value = { loading: false, error: 'cron 必填', next: [] }
    return
  }
  formCronPreview.value = { loading: true, error: '', next: [] }
  cronTimer = setTimeout(async () => {
    try {
      const p = await cronPreview(expr, 5)
      if (seq !== cronSeq) return          // 旧响应丢弃
      formCronPreview.value = { loading: false, error: '', next: p.next ?? [] }
    } catch (e: any) {
      if (seq !== cronSeq) return
      formCronPreview.value = { loading: false,
        error: `cron 非法：${String(e?.message ?? e)}`, next: [] }
    }
  }, 300)
})
onBeforeUnmount(() => { if (cronTimer) clearTimeout(cronTimer) })

function blankForm(): void {
  form.code = ''; form.name = ''; form.target_code = ''; form.job_type = ''
  form.cron = ''; form.misfire_policy = 'SKIP'; form.concurrency = 'FORBID'
  form.max_runtime_seconds = 3600
  form.blackout = []
  form.payload_template = ''
  form.note = ''
}
function openCreate(): void {
  formMode.value = 'create'
  blankForm()
  formError.value = ''; formConflict.value = false
  loadFormMeta()
  formOpen.value = true
}
function openEdit(sched: ScheduleItem): void {
  formMode.value = 'edit'
  editTarget.value = sched
  form.code = sched.code
  form.name = sched.name ?? ''
  form.target_code = sched.target_code ?? ''
  form.job_type = sched.job_type ?? ''
  form.cron = sched.cron ?? ''
  form.misfire_policy = sched.misfire_policy ?? 'SKIP'
  form.concurrency = sched.concurrency ?? 'FORBID'
  form.max_runtime_seconds = sched.max_runtime_seconds ?? 3600
  form.blackout = (sched.blackout ?? []).map((b: any) => ({ start: b.start, end: b.end }))
  form.payload_template = sched.payload_template != null
    ? JSON.stringify(sched.payload_template, null, 1) : ''
  form.note = sched.note ?? ''
  formError.value = ''; formConflict.value = false
  loadFormMeta()
  formOpen.value = true
}
async function loadFormMeta(): Promise<void> {
  if (!meta.value.job_types.length) {
    try { meta.value = await schedulesMeta() }
    catch { /* 保持空：不展示未注册类型 */ }
  }
  if (!targets.value.length) {
    try { targets.value = await listTargets() } catch { /* 下拉留空 */ }
  }
}
function askCloseForm(): void {
  if (form.code || form.name || form.cron) {
    if (!window.confirm('有未保存的输入，确认关闭并丢弃？')) return
  }
  formOpen.value = false
}
async function reloadForConflict(): Promise<void> {
  await load()
  const cur = rows.value.find(x => x.code === form.code)
  if (cur) editTarget.value = cur
}
function buildPayload(): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    code: form.code, name: form.name,
    target_code: form.target_code || '',
    job_type: form.job_type, cron: form.cron,
    misfire_policy: form.misfire_policy, concurrency: form.concurrency,
    max_runtime_seconds: Number(form.max_runtime_seconds) || 3600,
    blackout: form.blackout
      .filter(b => b.start.trim() || b.end.trim())
      .map(b => ({ start: b.start, end: b.end })),
    note: form.note,
  }
  const t = form.payload_template.trim()
  if (t) payload.payload_template = JSON.parse(t)
  else payload.payload_template = {}
  return payload
}
async function submitForm(): Promise<void> {
  formBusy.value = true
  formError.value = ''
  formConflict.value = false
  try {
    if (formMode.value === 'create') {
      await createSchedule(buildPayload())
      toast.success(`调度 ${form.code} 已创建（停用态；启用请走"启用…"）`)
    } else {
      const id = editTarget.value?.id
      if (!id) throw new Error('编辑目标丢失')
      await updateSchedule(id, { ...buildPayload(), revision: editTarget.value?.revision })
      toast.success('调度已保存（revision 乐观锁）')
    }
    formOpen.value = false
    await load()
    if (current.value) {
      current.value = rows.value.find(x => x.id === current.value?.id) ?? current.value
    }
  } catch (e: any) {
    if (e?.code === 'CONFLICT' || /revision mismatch/.test(String(e?.message))) {
      formConflict.value = true
      formError.value = '保存冲突（409）：他人已更新该调度。你的输入已保留；可拉取最新对照。'
    } else {
      formError.value = String(e?.message ?? e)   // 失败保留输入可重试
    }
  } finally { formBusy.value = false }
}
onMounted(load)
</script>

<style scoped>
.cron-cell { display: inline-flex; gap: 6px; align-items: center; }
button.mini { padding: 0 6px; font-size: 11px; }
.preview-box { font-size: 12px; margin-top: 4px; }
.preview-box ul { margin: 2px 0 0; padding-left: 16px; }
.preview-err { color: var(--c-danger); }
.kv { display: grid; grid-template-columns: 90px 1fr; gap: 4px 10px;
  font-size: 13px; margin-bottom: 10px; }
.mirror-bar { background: #f3e8ff; border: 1px solid #d8b4fe; color: #6b21a8;
  padding: 8px 12px; border-radius: 6px; font-size: 13px; margin: 10px 0; }
.actions { display: flex; gap: 8px; margin: 10px 0; }
.mini-table { width: 100%; font-size: 12px; border-collapse: collapse; }
.mini-table th, .mini-table td { text-align: left; padding: 5px 6px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
.load-more { margin-top: 8px; text-align: center; }
.load-more-err { font-size: 12px; color: var(--c-danger, #b91c1c); }
.small { font-size: 11px; display: block; }
.form-row { display: flex; gap: 10px; }
.form-row > span { flex: 1; }
.blackout-row { display: flex; gap: 6px; align-items: center; margin: 4px 0; }
.blackout-row input { width: 80px; }
.form-preview { border: 1px dashed var(--border, #e5e7eb); border-radius: 6px; padding: 6px; margin-top: 6px; }
.drawer-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.form-error { color: var(--c-danger, #b91c1c); font-size: 12px; margin: 8px 0 0; }
.muted { color: var(--text-muted, #6b7280); }
label { display: block; font-size: 12px; color: var(--text-muted, #6b7280); margin: 10px 0 2px; }
input, select, textarea { width: 100%; box-sizing: border-box; }
</style>
