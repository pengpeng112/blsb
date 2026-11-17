<!-- LT-07 医保联调台（/targets/medins_emr_sd 联调台 tab）：
     交易选择/样例载入/编辑/本地校验/Mock 发送（进程内）/执行包导出/结果回填。
     顶部黄条：本页不向医保平台发送任何请求。 -->
<template>
  <div class="medins-debug">
    <div class="yellow-bar">
      本页不会向医保平台发送任何请求。真实联调请导出执行包在院内授权环境执行，并回填结果。
    </div>

    <div class="md-cols">
      <!-- 左：交易列表 -->
      <div class="md-left card">
        <input v-model="search" placeholder="搜索交易号/名称" />
        <ul class="txn-list">
          <li v-for="t in filteredTxns" :key="t.code"
              :class="{ active: t.code === code }" @click="pick(t.code)">
            <code>{{ t.code }}</code> {{ t.name }}
            <span v-if="!t.has_fields" class="muted">（无字段表）</span>
          </li>
        </ul>
      </div>

      <!-- 中：编辑器 -->
      <div class="md-mid card">
        <div class="md-toolbar">
          <button class="ghost" @click="loadSample" :disabled="!code">载入合成样例</button>
          <button class="ghost" @click="formatJson">格式化</button>
        </div>
        <textarea v-model="raw" class="json-editor" spellcheck="false"
                  placeholder="报文 JSON（先选择交易）"></textarea>
        <div class="md-actions">
          <button :disabled="busy" @click="doValidate">校验</button>
          <button :disabled="busy || !code" @click="doSendMock">发送到 Mock</button>
          <button :disabled="busy || !code" @click="doExport">导出院内执行包</button>
        </div>
      </div>

      <!-- 右：结果+契约 -->
      <div class="md-right card">
        <div v-if="errors.length">
          <b>校验结果（{{ hardCount }} 错误 / {{ warnCount }} 提示）</b>
          <ul class="err-list">
            <li v-for="(e, i) in errors" :key="i"
                :class="e.level">
              <code>{{ e.path }}</code> {{ e.code }}：{{ e.message }}
            </li>
          </ul>
        </div>
        <p v-else-if="validated" class="ok-line">校验通过（长度单位待确认项见提示）</p>
        <div v-if="lastResponse">
          <b>Mock 响应（合成）</b>
          <JsonViewer :data="lastResponse" />
        </div>
        <details>
          <summary>字段契约（树形·完整路径搜索）</summary>
          <input v-model="fieldSearch" placeholder="搜索路径/名称（如 diseinfo[].diag_code）" />
          <table class="mini-table">
            <thead><tr><th>路径</th><th>名称</th><th>类型</th><th>长度</th><th>必填</th></tr></thead>
            <tbody>
              <tr v-for="f in filteredFields" :key="f.path">
                <td><code :style="{ paddingLeft: (f.depth * 10) + 'px' }">{{ f.path }}</code></td>
                <td>{{ f.name }}</td>
                <td>{{ f.type }}</td>
                <td>{{ f.max_len ?? '—' }}</td>
                <td>{{ f.required ? 'Y' : '' }}</td>
              </tr>
            </tbody>
          </table>
        </details>
      </div>
    </div>

    <!-- 导出结果（浏览器下载，不落库） -->
    <Drawer :open="!!exportPack" title="院内执行包" @close="exportPack = null">
      <template v-if="exportPack">
        <p class="muted">{{ exportPack.notice }}</p>
        <p class="muted small">交易 <code>{{ exportPack.transaction_code }}</code>
          · Profile <code>{{ exportPack.profile_revision?.revision }}</code>
          (schema {{ exportPack.profile_revision?.schema_version }})</p>
        <b>公共报文头（envelope）</b>
        <JsonViewer :data="splitPack.envelope" />
        <b>业务 input（真实值仅本次下载，不落库）</b>
        <JsonViewer :data="splitPack.input" />
        <b>HTTP 头</b>
        <pre class="pack-pre">{{ JSON.stringify(exportPack.headers, null, 1) }}</pre>
        <b>curl 模板</b>
        <pre class="pack-pre">{{ exportPack.curl }}</pre>
        <button @click="downloadPack">下载 JSON</button>
        <button class="ghost" @click="openRecord(exportPack.record_id)">回填院内执行结果…</button>
      </template>
    </Drawer>

    <!-- 回填 -->
    <Modal :open="recording" title="回填院内执行结果（人工证据）"
           @close="recording = false">
      <label><input type="checkbox" v-model="rec.success" /> 执行成功（发送成功）</label>
      <label>平台返回码</label>
      <input v-model="rec.platform_code" placeholder="如 0 / -1" />
      <label>说明（≥5 字）</label>
      <textarea v-model="rec.note" rows="2"></textarea>
      <div class="modal-actions">
        <button class="ghost" @click="recording = false">取消</button>
        <button :disabled="recBusy || rec.note.trim().length < 5"
                @click="submitRecord">提交回填</button>
      </div>
    </Modal>

    <!-- 历史（RT-04 B4：每行回填按钮，角色/范围与后端一致；RT-03 加载更多保留） -->
    <h4>联调历史</h4>
    <table v-if="history.length" class="mini-table">
      <thead><tr><th>时间</th><th>交易</th><th>模式</th><th>结果</th><th>说明</th><th></th></tr></thead>
      <tbody>
        <tr v-for="r in history" :key="r.id">
          <td><TimeText :value="r.created_at" /></td>
          <td><code>{{ r.code }}</code></td>
          <td>{{ r.mode }}</td>
          <td><StatusTag :status="r.outcome || '—'" /></td>
          <td class="muted">{{ (r.note || '').slice(0, 60) }}</td>
          <td><button v-if="canRecord" class="ghost small-btn"
                      @click="openRecord(r.id)">回填…</button></td>
        </tr>
      </tbody>
    </table>
    <EmptyState v-else text="暂无联调记录" />
    <div v-if="historyHasMore" class="load-more">
      <button class="ghost" :disabled="historyLoadingMore" @click="loadMoreHistory">
        {{ historyLoadingMore ? '加载中…' : '加载更多' }}</button>
    </div>
    <p v-else-if="history.length" class="muted small">已加载全部联调记录</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { listMedinsTxns, getMedinsTxn, validateMedins, sendMedinsMock,
  exportMedinsRequest, listMedinsExchanges, recordMedinsResult,
  medinsApi } from '../../api/medins'
import type { MedinsExchange } from '../../api/medins'
import StatusTag from '../../components/StatusTag.vue'
import JsonViewer from '../../components/JsonViewer.vue'
import Drawer from '../../components/Drawer.vue'
import Modal from '../../components/Modal.vue'
import EmptyState from '../../components/EmptyState.vue'
import TimeText from '../../components/TimeText.vue'
import { toast } from '../../components/Toast'
import { mergePagedItems } from '../../utils/paging'
import { flattenContractTree, filterTreeRows, splitEnvelopeInput }
  from '../../utils/contractTree'
import type { ContractRow } from '../../utils/contractTree'

const txns = ref<any[]>([])
const search = ref('')
const code = ref('')
const fieldRows = ref<ContractRow[]>([])
const raw = ref('')
const errors = ref<any[]>([])
const validated = ref(false)
const lastResponse = ref<any>(null)
const exportPack = ref<any>(null)
const history = ref<MedinsExchange[]>([])
const historyCursor = ref<string | null>(null)   // RT-03 S08：keyset 游标
const historyLoadingMore = ref(false)
const historyHasMore = computed(() => historyCursor.value != null)
const busy = ref(false)
const recording = ref(false)
const recBusy = ref(false)
const rec = ref({ success: false, platform_code: '', note: '' })
const recordTargetId = ref('')
const fieldSearch = ref('')

const filteredTxns = computed(() => {
  const s = search.value.trim().toLowerCase()
  if (!s) return txns.value
  return txns.value.filter(t =>
    t.code.includes(s) || (t.name || '').toLowerCase().includes(s))
})
const filteredFields = computed(() =>
  filterTreeRows(fieldRows.value, fieldSearch.value))
/** B4：角色/范围与后端一致（operator/platform_admin 可回填）。 */
const canRecord = computed(() => {
  let roles: string[] = []
  try { roles = JSON.parse(localStorage.getItem('blyb.roles') || '[]') } catch { roles = [] }
  return roles.includes('operator') || roles.includes('platform_admin')
})
/** 导出包拆分：公共报文头与业务 input 分开展示。 */
const splitPack = computed(() => splitEnvelopeInput(exportPack.value?.message))
const hardCount = computed(() =>
  errors.value.filter(e => e.level === 'error').length)
const warnCount = computed(() =>
  errors.value.filter(e => e.level !== 'error').length)

function parseMessage(): any | null {
  try { return JSON.parse(raw.value) }
  catch (e: any) { toast.error(`JSON 解析失败：${e.message}`); return null }
}

async function pick(c: string): Promise<void> {
  code.value = c
  errors.value = []
  validated.value = false
  lastResponse.value = null
  const d = await getMedinsTxn(c)
  // RT-04：树形契约（schema_version=2 children；旧平面 nodes 兼容）
  fieldRows.value = flattenContractTree(d.fields)
}
async function loadSample(): Promise<void> {
  const d = await getMedinsTxn(code.value)
  if (d.sample) raw.value = JSON.stringify(d.sample, null, 1)
}
function formatJson(): void {
  const m = parseMessage()
  if (m) raw.value = JSON.stringify(m, null, 1)
}
async function doValidate(): Promise<void> {
  const m = parseMessage(); if (!m || !code.value) return
  busy.value = true
  try {
    const r = await validateMedins(code.value, m)
    errors.value = r.errors
    validated.value = true
    loadHistory()
  } catch (e: any) { toast.error(String(e?.message ?? e)) }
  finally { busy.value = false }
}
async function doSendMock(): Promise<void> {
  const m = parseMessage(); if (!m) return
  busy.value = true
  try {
    const r = await sendMedinsMock(code.value, m)
    lastResponse.value = r.response
    loadHistory()
  } catch (e: any) { toast.error(String(e?.message ?? e)) }
  finally { busy.value = false }
}
async function doExport(): Promise<void> {
  const m = parseMessage(); if (!m) return
  busy.value = true
  try {
    exportPack.value = await exportMedinsRequest(code.value, m)
    loadHistory()
  } catch (e: any) { toast.error(String(e?.message ?? e)) }
  finally { busy.value = false }
}
function downloadPack(): void {
  if (!exportPack.value) return
  const blob = new Blob(
    [JSON.stringify(exportPack.value.message, null, 1)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `request-${code.value}.json`
  a.click()
  URL.revokeObjectURL(a.href)
}
function openRecord(id: string): void {
  recordTargetId.value = id
  rec.value = { success: false, platform_code: '', note: '' }
  recording.value = true
}
async function submitRecord(): Promise<void> {
  recBusy.value = true
  try {
    await recordMedinsResult(recordTargetId.value, rec.value)
    recording.value = false
    toast.success('执行结果已回填（人工证据）')
    await refreshRowAfterRecord(recordTargetId.value, rec.value.success)
  } catch (e: any) {
    toast.error(String(e?.message ?? e))
    // 错误保留表单（不清空、不关弹窗）
  } finally { recBusy.value = false }
}
/** 提交后刷新该行：服务端首页重取合并；不在首页时按回填结果如实更新
 * 该行 outcome（失败回填显示 FAIL，不能只有成功更新）。 */
async function refreshRowAfterRecord(id: string, success: boolean):
  Promise<void> {
  try {
    const resp = await listMedinsExchanges(undefined, undefined, 50)
    const fresh = resp.items.find((r: MedinsExchange) => r.id === id)
    if (fresh) {
      const idx = history.value.findIndex(r => r.id === id)
      if (idx >= 0) history.value[idx] = fresh
      return
    }
  } catch { /* 刷新失败退回本地反映 */ }
  const idx = history.value.findIndex(r => r.id === id)
  if (idx >= 0) history.value[idx] = {
    ...history.value[idx], outcome: success ? 'PASS' : 'FAIL' }
}
/** 首页加载（重置游标）；reset=false 时按游标翻页合并（失败静默保留旧表）。 */
async function loadHistory(reset = true): Promise<void> {
  try {
    const cursor = reset ? undefined : (historyCursor.value ?? undefined)
    const resp = await listMedinsExchanges(undefined, cursor)
    history.value = reset ? resp.items
      : mergePagedItems(history.value, resp.items)
    historyCursor.value = resp.next_cursor ?? null
  } catch { /* 空表 */ }
}
async function loadMoreHistory(): Promise<void> {
  if (!historyCursor.value || historyLoadingMore.value) return
  historyLoadingMore.value = true
  try { await loadHistory(false) }
  finally { historyLoadingMore.value = false }
}
onMounted(async () => {
  txns.value = await listMedinsTxns()
  loadHistory()
})
</script>

<style scoped>
.yellow-bar { background: #fffbeb; border: 1px solid #fde68a; color: #92400e;
  padding: 8px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 10px; }
.md-cols { display: grid; grid-template-columns: 240px 1fr 340px; gap: 10px; }
@media (max-width: 1100px) { .md-cols { grid-template-columns: 1fr; } }
.txn-list { list-style: none; padding: 0; margin: 6px 0 0; max-height: 420px;
  overflow: auto; font-size: 13px; }
.txn-list li { padding: 4px 6px; cursor: pointer; border-radius: 4px; }
.txn-list li.active, .txn-list li:hover { background: var(--bg-alt, #f3f4f6); }
.json-editor { width: 100%; min-height: 300px; font-family: monospace;
  font-size: 12px; box-sizing: border-box; }
.md-toolbar { display: flex; gap: 8px; margin-bottom: 6px; }
.md-actions { display: flex; gap: 8px; margin-top: 8px; }
.err-list { list-style: none; padding-left: 0; font-size: 12px;
  max-height: 220px; overflow: auto; }
.err-list li.error { color: #b91c1c; }
.err-list li.warn { color: #92400e; }
.ok-line { color: #15803d; }
.mini-table { width: 100%; font-size: 12px; border-collapse: collapse; }
.mini-table th, .mini-table td { text-align: left; padding: 4px 6px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
.pack-pre { background: var(--bg-alt, #fafafa); padding: 8px; font-size: 12px;
  max-height: 200px; overflow: auto; white-space: pre-wrap; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
.load-more { margin-top: 8px; text-align: center; }
.small-btn { font-size: 12px; padding: 2px 8px; }
label { display: block; font-size: 13px; margin: 8px 0 2px; }
</style>
