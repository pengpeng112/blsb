<!-- LT-06B 台账面板（详情页/独立页共用）：先筛后限游标分页 + UNKNOWN 人工处置。
     全部条件传后端（无前端过滤）；游标换条件自动失效由后端 400 保证（提示后重置）。
     RT-01：① 接收 /ledger?bucket= 深链初始过滤并双向同步 URL；② 行详情加载
     失败只写 detailError（Drawer 内展示），不再清空主列表；③ 药品行提供直跳
     /drugtrace 按钮（替代 window.alert）；④ 处置失败用 toast；⑤ 时间统一 TimeText。 -->
<template>
  <div>
    <FilterBar :busy="loading" @search="reload" @reset="reset">
      <select v-model="bucket">
        <option value="">全部状态</option>
        <option v-for="b in BUCKETS" :key="b" :value="b">{{ b }}</option>
      </select>
      <input v-model="dateFrom" type="date" title="开始日期（院内）" />
      <input v-model="dateTo" type="date" title="结束日期（院内）" />
      <input v-model="q" placeholder="单号/提交ID 前缀" />
    </FilterBar>

    <div class="meta-line muted">
      共约 {{ page?.total_estimate ?? '—' }} 条（{{ dataSourceLabel }}）
    </div>

    <DataTable :columns="cols" :rows="rows" :loading="loading && !rows.length"
               :error="error" @retry="reload" :has-more="!!cursor"
               :loading-more="loadingMore" @load-more="loadMore"
               @row-click="openDetail" empty-text="无匹配记录" />

    <Drawer :open="detailOpen" :title="detailTitle" @update:open="onDetailClose"
            @close="onDetailClose">
      <!-- PT-03：外部副本观测记录（传染病等 EXTERNAL 业务）——结构化中文详情
           （旧版模板读不到 platform Submission 字段导致抽屉空白） -->
      <template v-if="detail?.kind === 'external_record'">
        <h4>
          文书：{{ detail.doc_type_name || '未分类文书' }}
          <code v-if="detail.doc_type_code">（{{ detail.doc_type_code }}）</code>
        </h4>
        <p>记录键 <code>{{ detail.record_key }}</code></p>
        <p>当前状态：<StatusTag :status="detail.bucket || 'UNKNOWN'" />
          <span class="muted">{{ bucketCn(detail.bucket) }}</span></p>
        <p>证据等级：{{ evidenceCn(detail.evidence_level) }}</p>
        <ul class="ext-facts">
          <li>源业务时间：{{ detail.source_op_time ? formatTime(detail.source_op_time) : '（回执未携带，未知）' }}</li>
          <li>最近推送时间：{{ detail.last_attempt_at ? formatTime(detail.last_attempt_at) : '（未知，不冒充）' }}</li>
          <li>累计尝试（可见下界）：{{ detail.attempts_lower_bound }} 次</li>
          <li>来源实例：{{ detail.source_instance }}</li>
          <li>导入时间：{{ detail.imported_at ? formatTime(detail.imported_at) : '—' }}</li>
        </ul>
        <template v-if="detail.last_error_code || detail.last_error_message">
          <b>异常诊断</b>
          <p class="diag">{{ errorCodeCn(detail.last_error_code, detail.raw_state) }}</p>
          <p class="muted" v-if="detail.last_error_message">详情：{{ detail.last_error_message }}</p>
        </template>
        <p class="muted small">{{ detail.note }}</p>
      </template>
      <template v-else-if="detail">
        <h4>状态：<StatusTag :status="detail.state" /></h4>
        <p class="muted">业务键 {{ detail.idempotency_key }}</p>
        <p class="muted">快照摘要 {{ detail.snapshot_digest?.slice(0, 16) }}…
          冻结请求可核对（处置前提）</p>
        <b>尝试记录</b>
        <ul class="att-list">
          <li v-for="a in detail.attempts ?? []" :key="a.attempt_no">
            #{{ a.attempt_no }} {{ a.outcome }}
            <span class="muted">{{ a.http_status ?? '' }}
              {{ a.response_snapshot?.error_message ?? '' }}</span>
          </li>
          <li v-if="!(detail.attempts ?? []).length" class="muted">（无尝试记录）</li>
        </ul>
        <b>回执</b>
        <JsonViewer :data="detail.receipts ?? []" />
        <template v-if="detail.state === 'UNKNOWN' && canResolve">
          <b>人工处置</b>
          <p class="muted">须已核对冻结请求与平台侧证据；允许重发仅回到待发送，
            不自动发送。</p>
          <form class="resolve-form" @submit.prevent="submitResolve">
            <select v-model="resolution" required>
              <option value="CONFIRMED">已确认（平台侧成功）</option>
              <option value="REJECTED">被拒（平台侧拒绝）</option>
              <option value="RESEND_ALLOWED">允许重发（回到待发送）</option>
            </select>
            <textarea v-model="reason" rows="2" placeholder="处置理由（≥5 字）"
                      required></textarea>
            <input v-model="evidenceRef" placeholder="证据引用（如回执编号/工单）" />
            <button type="submit" :disabled="resolving">提交处置</button>
          </form>
        </template>
      </template>
      <template v-else-if="detailRouteHint">
        <div class="route-hint">
          <p class="muted">该业务详情请前往专页查看（受控流程）。</p>
          <button @click="goDomainPage">前往业务专页</button>
        </div>
      </template>
      <ErrorState v-else-if="detailError" :message="detailError" compact />
    </Drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ledgerSearch, ledgerDetail, manualResolution } from '../../api/index'
import type { LedgerRow } from '../../api/types'
import FilterBar from '../../components/FilterBar.vue'
import DataTable from '../../components/DataTable.vue'
import Drawer from '../../components/Drawer.vue'
import JsonViewer from '../../components/JsonViewer.vue'
import StatusTag from '../../components/StatusTag.vue'
import ErrorState from '../../components/ErrorState.vue'
import { toast } from '../../components/Toast'
import { formatHospitalTime } from '../../utils/time'

const props = defineProps<{ target: string, canResolve?: boolean,
  initialBucket?: string }>()

const route = useRoute()
const router = useRouter()

const BUCKETS = ['PENDING', 'SENDING', 'ACCEPTED', 'CONFIRMED', 'PARTIAL',
  'REJECTED', 'UNKNOWN', 'NOT_REPORTABLE']
const bucket = ref(props.initialBucket ?? '')
const dateFrom = ref('')
const dateTo = ref('')
const q = ref('')
const rows = ref<(LedgerRow & { state?: string })[]>([])
const page = ref<any>(null)
const cursor = ref<string | null>(null)
const loading = ref(false)
const loadingMore = ref(false)
// RT-01：主列表错误与详情抽屉错误严格隔离——行详情失败绝不把整张表清空
const error = ref('')
const detailError = ref('')
const detail = ref<any>(null)
const detailRouteHint = ref<string | null>(null)
const detailRow = ref<LedgerRow | null>(null)
const resolution = ref('CONFIRMED')
const reason = ref('')
const evidenceRef = ref('')
const resolving = ref(false)

const detailOpen = computed(() =>
  !!(detail.value || detailError.value || detailRouteHint.value))
const detailTitle = computed(() =>
  `提交 ${detailRow.value?.id?.slice(0, 8) ?? detail.value?.id?.slice(0, 8) ?? ''}`)

// RT-01：/ledger 深链 bucket 双向同步（外部变化应用；本地选择回写 URL）
const syncQuery = computed(() => route.path === '/ledger')
watch(() => route.query.bucket, v => {
  const nb = String(v ?? '')
  if (nb !== bucket.value && BUCKETS.includes(nb)) {
    bucket.value = nb
    reload()
  }
})
watch(bucket, v => {
  if (!syncQuery.value) return
  const query: Record<string, string> = { ...route.query as any }
  if (v) query.bucket = v
  else delete query.bucket
  router.replace({ query })
})

const cols = [
  { key: 'business_key', title: '业务单号' },
  { key: 'bucket', title: '状态', slot: undefined, formatter: (r: any) => r.bucket },
  { key: 'raw_state', title: '原始状态' },
  { key: 'attempts', title: '尝试', align: 'right' as const },
  { key: 'last_error_code', title: '错误码', formatter: (r: any) =>
    r.last_error_code ?? '—' },
  { key: 'last_error_message', title: '最后错误', formatter: (r: any) => {
    const s = r.last_error_message ?? ''
    return s.length > 40 ? s.slice(0, 40) + '…' : (s || '—') } },
  { key: 'updated_at', title: '更新时间', formatter: (r: any) =>
    formatHospitalTime(r.updated_at) || '—' },
]

// PT-03：外部记录详情中文释义（文书状态桶/证据等级/错误码与阶段诊断）
function bucketCn(b?: string): string {
  return ({ PENDING: '待处理（等待父表受理）', SENDING: '发送中',
    ACCEPTED: '前置机已受理', CONFIRMED: '国家平台已确认',
    PARTIAL: '部分成功', REJECTED: '被退回', UNKNOWN: '结果未知',
    NOT_REPORTABLE: '不上报' } as Record<string, string>)[b ?? ''] ?? (b ?? '—')
}
function evidenceCn(e?: string): string {
  return ({ FRONTEND_ACCEPTED: '前置机回执实证（受理≠国家平台终审）',
    SENT_LEDGER_ONLY: '已发账实证（已受理推送，回执副本未含该条）',
    CLIENT_EVIDENCE: '本地回执实证', PARENT_WAITING: '主子门禁保护中',
    MOCK_ONLY: '演练记录（Mock，未真实发送）',
    INCONCLUSIVE: '结果不确定（待下一班次确认）'
  } as Record<string, string>)[e ?? ''] ?? (e ?? '—')
}
function errorCodeCn(code?: string | null, rawState?: string): string {
  if (rawState === 'PARENT_WAIT') return '等待父表主记录受理：父单成功后下一班次自动补发，无需人工处理'
  if (rawState === 'CLIENT_INVALID') return '本地必填预检未过：源病历/视图字段缺失，进入质量等待（不发送不完整数据）'
  const m: Record<string, string> = {
    '00': '受理成功',
    '01': '平台服务异常：已自动重试，持续失败请联系前置机运维',
    '02': '字典/值域不符：核对源字段与上报字典映射',
    '03': '数据格式错误：核对日期/编码格式（如日期须为 YYYY-MM-DD HH:mm:ss）',
    '04': '平台必填项校验未过：请核查源病历或视图字段（缺必填的字段清单见错误详情）',
    '05': '时间格式无法解析：核对源时间字段',
    '99': '平台未知异常：已自动重试',
  }
  return m[String(code ?? '').trim()] ?? '未知错误码'
}
function formatTime(v?: string | null): string {
  if (!v) return '—'
  try {
    return formatHospitalTime(v) ?? v
  } catch { return v }
}

const DS_LABELS: Record<string, string> = {
  platform: '平台数据', external_ledger: '导入副本', none: '无数据源' }
const dataSourceLabel = () =>
  DS_LABELS[page.value?.data_source ?? ''] ?? page.value?.data_source ?? '—'

function params(cursorV?: string) {
  const out: Record<string, string> = { target: props.target }
  if (bucket.value) out.bucket = bucket.value
  if (dateFrom.value) out.date_from = dateFrom.value
  if (dateTo.value) out.date_to = dateTo.value
  if (q.value) out.q = q.value
  if (cursorV) out.cursor = cursorV
  return out
}

async function reload(): Promise<void> {
  cursor.value = null
  rows.value = []
  loading.value = true
  error.value = ''
  try {
    page.value = await ledgerSearch(props.target, {
      bucket: bucket.value || undefined,
      date_from: dateFrom.value || undefined,
      date_to: dateTo.value || undefined,
      q: q.value || undefined, limit: 50 })
    rows.value = page.value.items ?? []
    cursor.value = page.value.next_cursor
  } catch (e: any) {
    error.value = String(e?.message ?? e)
  } finally {
    loading.value = false
  }
}

async function loadMore(): Promise<void> {
  if (!cursor.value) return
  loadingMore.value = true
  try {
    const next = await ledgerSearch(props.target, {
      bucket: bucket.value || undefined,
      date_from: dateFrom.value || undefined,
      date_to: dateTo.value || undefined,
      q: q.value || undefined, cursor: cursor.value ?? undefined, limit: 50 })
    rows.value.push(...(next.items ?? []))
    cursor.value = next.next_cursor
  } catch (e: any) {
    error.value = String(e?.message ?? e)
  } finally {
    loadingMore.value = false
  }
}

function reset(): void {
  bucket.value = ''; dateFrom.value = ''; dateTo.value = ''; q.value = ''
  reload()
}

async function openDetail(row: LedgerRow): Promise<void> {
  detail.value = null
  detailError.value = ''
  detailRouteHint.value = null
  detailRow.value = row
  try {
    const d = await ledgerDetail(props.target, row.id)
    if (d?.detail_route) {
      // RT-01：药品等专页业务——抽屉内提示并直跳，不再 window.alert
      detailRouteHint.value = String(d.detail_route)
      return
    }
    detail.value = d
  } catch (e: any) {
    detailError.value = String(e?.message ?? e)
  }
}
function goDomainPage(): void {
  const to = detailRouteHint.value || '/drugtrace'
  detailRouteHint.value = null
  router.push(to)
}
function onDetailClose(): void {
  detail.value = null
  detailError.value = ''
  detailRouteHint.value = null
  detailRow.value = null
}

async function submitResolve(): Promise<void> {
  if (!detail.value) return
  resolving.value = true
  try {
    await manualResolution(props.target, detail.value.id, {
      resolution: resolution.value as any,
      reason: reason.value, evidence_ref: evidenceRef.value,
      expected_state: 'UNKNOWN' })
    onDetailClose()
    reason.value = ''
    evidenceRef.value = ''
    toast.success('人工处置已提交（已记审计）')
    reload()
  } catch (e: any) {
    toast.error(`处置失败：${e?.message ?? e}`)
  } finally {
    resolving.value = false
  }
}

onMounted(reload)
</script>

<script lang="ts">
export default { name: 'LedgerPane' }
</script>

<style scoped>
.meta-line { font-size: 12px; margin: 6px 0; }
.att-list { font-size: 12px; list-style: none; padding-left: 0; }
.resolve-form { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.route-hint { display: grid; gap: 10px; justify-items: start; padding: 8px 0; }
.ext-facts { font-size: 12px; list-style: none; padding-left: 0; }
.diag { font-size: 13px; color: #92400e; background: #fffbeb;
  border: 1px solid #fde68a; border-radius: 6px; padding: 6px 10px; }
.small { font-size: 12px; }
</style>
