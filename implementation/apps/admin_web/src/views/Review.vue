<!-- RT-05 U09 Review：自动加载待复核 acquisitions（可搜索点选）→ 展示候选
     与文件/PIS 证据 → 理由确认后绑定（If-Match 乐观锁）。
     0/1/N 分流语义后端为准；未知不自动绑定；不强迫手填 UUID（列表点选为主，
     手输为备用）。 -->
<template>
  <div>
    <PageHeader title="关联复核" subtitle="歧义条码人工选择：必须给出理由；只作用于我方纠正层" />
    <div class="two-col">
      <!-- 左：待复核采集列表（可搜索点选） -->
      <div class="card">
        <b>采集事件（点击查看候选）</b>
        <FilterBar :busy="listLoading" @search="loadAcqs" @reset="kw = ''; loadAcqs()">
          <label>搜索
            <input v-model.trim="kw" placeholder="条码 / 扫描ID / 采集ID" />
          </label>
        </FilterBar>
        <LoadingBlock v-if="listLoading" />
        <ErrorState v-else-if="listErr" :message="listErr" @retry="loadAcqs" />
        <EmptyState v-else-if="!acqs.length" text="暂无采集事件" />
        <table v-else class="mini-table">
          <thead><tr><th>采集</th><th>来源扫描</th><th>状态</th><th></th></tr></thead>
          <tbody>
            <tr v-for="a in acqs" :key="a.id" :class="{ sel: a.id === acqId }"
                @click="pick(a)">
              <td><code>{{ a.id.slice(0, 8) }}</code></td>
              <td>{{ a.source_scan_id || '—' }}</td>
              <td><StatusTag :status="a.state" /></td>
              <td>
                <span v-if="candCount[a.id]" class="cand-badge">{{ candCount[a.id] }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 右：候选与证据 -->
      <div class="card">
        <b>关联候选 {{ acqId ? `（采集 ${acqId.slice(0, 8)}）` : '' }}</b>
        <EmptyState v-if="!acqId" text="先在左侧选择采集事件（不强迫手填 UUID）" />
        <template v-else>
          <LoadingBlock v-if="candLoading" />
          <ErrorState v-else-if="candErr" :message="candErr" @retry="loadCandidates" />
          <EmptyState v-else-if="!cands.length" text="无候选（0 候选：等待匹配或已唯一确定）" />
          <template v-else>
            <p class="muted small">
              决策版本 v{{ version }}（If-Match 乐观锁）；候选唯一≠自动绑定——
              0/1/N 分流以后端为准，未知不自动绑定。</p>
            <table class="mini-table">
              <thead><tr><th>候选（来源键）</th><th>匹配依据</th><th>状态</th><th>绑定操作</th></tr></thead>
              <tbody>
                <tr v-for="c in cands" :key="c.id">
                  <td><code>{{ c.candidate_ref }}</code></td>
                  <td class="muted">
                    {{ c.match_basis?.method }}（truncated={{ c.match_basis?.truncated }}）</td>
                  <td><StatusTag :status="c.candidate_state" /></td>
                  <td>
                    <input v-model="reason[c.id]" placeholder="绑定理由（必填）"
                           style="width:150px" />
                    <button class="ghost" :disabled="!reason[c.id] || binding[c.id]"
                            @click="bind(c)">
                      {{ binding[c.id] ? '绑定中…' : '确认绑定' }}</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <!-- 文件/PIS 证据（来源侧；只读展示） -->
            <details v-if="evidenceText" class="ev-box">
              <summary class="muted">文件 / PIS 证据（来源侧只读）</summary>
              <pre class="ev-pre">{{ evidenceText }}</pre>
            </details>
            <p v-if="msg" :class="msgErr ? 'err' : 'muted'">{{ msg }}</p>
          </template>
        </template>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import FilterBar from '../components/FilterBar.vue'
import StatusTag from '../components/StatusTag.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import { toast } from '../components/Toast'

const kw = ref('')
const acqs = ref<any[]>([])
const listLoading = ref(true)
const listErr = ref('')
const acqId = ref('')
const cands = ref<any[]>([])
const candLoading = ref(false)
const candErr = ref('')
const version = ref(0)
const candCount = reactive<Record<string, number>>({})
const reason = reactive<Record<string, string>>({})
const binding = reactive<Record<string, boolean>>({})
const msg = ref('')
const msgErr = ref(false)
const evidenceText = ref('')

async function loadAcqs(): Promise<void> {
  listLoading.value = true
  listErr.value = ''
  try {
    const q = kw.value ? `?q=${encodeURIComponent(kw.value)}` : ''
    const d = await api.coreGet('/api/v1/acquisitions' + q)
    acqs.value = Array.isArray(d) ? d : (d.items ?? d.acquisitions ?? [])
    if (!Array.isArray(acqs.value)) acqs.value = []
  } catch (e: any) { listErr.value = String(e?.message ?? e) }
  finally { listLoading.value = false }
}
async function pick(a: any): Promise<void> {
  acqId.value = a.id
  await loadCandidates()
  // 证据：从采集详情取（文件清单/身份提示）——失败不清空候选区
  try {
    const d = await api.coreGet(`/api/v1/acquisitions/${a.id}`)
    evidenceText.value = JSON.stringify({
      identity_hints: d.identity_hints ?? d.identity ?? null,
      files: d.files ?? d.manifest?.files ?? null,
      completion_evidence: d.completion_evidence ?? null,
    }, null, 1)
  } catch { evidenceText.value = '' }
}
async function loadCandidates(): Promise<void> {
  if (!acqId.value) return
  candLoading.value = true
  candErr.value = ''
  cands.value = []
  try {
    const r = await api.coreGet(
      `/api/v1/pathology/acquisitions/${acqId.value}/association-candidates`)
    cands.value = r.candidates ?? []
    version.value = r.decision_version ?? 0
    candCount[acqId.value] = cands.value.length
  } catch (e: any) { candErr.value = String(e?.message ?? e) }
  finally { candLoading.value = false }
}
async function bind(c: any): Promise<void> {
  const r = reason[c.id]
  if (!r || r.trim().length < 1) return
  binding[c.id] = true
  msgErr.value = false
  try {
    const slideKey = c.candidate_ref.split('#slide:')[1]
    const barcode = c.source_ref.split('barcode:')[1]
    await api.corePost('/api/v1/manual-decisions', {
      entity_ref: `acquisition:${acqId.value}`, action: 'bind_slide',
      after: { source_slide_id: slideKey, barcode_raw: barcode }, reason: r,
    }, undefined, { 'If-Match': String(version.value) })
    msg.value = '已绑定并触发重评估'
    msgErr.value = false
    toast.success('已绑定（人工决定已审计）')
    await loadCandidates()
  } catch (e: any) {
    msg.value = `绑定失败：${e.message}（理由已保留）`   // 失败保留输入
    msgErr.value = true
  } finally { binding[c.id] = false }
}
onMounted(loadAcqs)
</script>

<style scoped>
.two-col { display: grid; grid-template-columns: 1fr 1.3fr; gap: 12px; }
@media (max-width: 1000px) { .two-col { grid-template-columns: 1fr; } }
.card { background: var(--c-surface, #fff); border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px; padding: 12px; }
.mini-table { width: 100%; font-size: 13px; border-collapse: collapse; margin: 6px 0; }
.mini-table th, .mini-table td { text-align: left; padding: 5px 6px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
tbody tr { cursor: pointer; }
tbody tr.sel { background: var(--bg-alt, #f0f7ff); }
.cand-badge { background: #dbeafe; color: #1e40af; border-radius: 10px;
  font-size: 11px; padding: 1px 7px; }
.ev-box { margin-top: 8px; }
.ev-pre { background: var(--bg-alt, #fafafa); font-size: 11px; max-height: 220px;
  overflow: auto; white-space: pre-wrap; }
.err { color: var(--c-danger, #b91c1c); font-size: 12px; }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
</style>
