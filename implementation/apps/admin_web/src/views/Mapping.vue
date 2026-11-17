<!-- RT-05 U05：字段映射可视化与无损高级模式。
     - 8 个真实算子（mapping_engine ALLOWED_OPS 逐字一致；旧计划 coalesce
       不是当前算子，UI 不发明）；参数名用 source_field 等真实字段；
     - JSON↔表格无损（未知扩展键 _extra 保留；advancedOnly 文档不重组）；
     - 字典引用必须显式 namespace+version（批准状态可见，禁止 latest 漂移）；
     - 模拟调真实 simulate，前端不伪造试算结果；"填入合成示例"有标识；
     - 服务端 validate_mapping 白名单仍为最终防线。 -->
<template>
  <div>
    <PageHeader title="字段映射" subtitle="受限 DSL（8 算子）+ 字典引用 + 模拟（服务端试算）" />
    <div class="hint-bar">向导：连接器配置 → 本页字段映射/模拟 → 字典批准（医院配置包页）→ Pack 发布 ConfigRelease。</div>

    <!-- 修订列表 -->
    <div class="card">
      <b>映射修订</b>
      <LoadingBlock v-if="rowsLoading" />
      <ErrorState v-else-if="rowsError" :message="rowsError" @retry="load" />
      <EmptyState v-else-if="!rows.length" text="暂无映射（先在下方创建）" />
      <table v-else class="mini-table">
        <thead><tr><th>mapping_key</th><th>rev</th><th>状态</th><th>字典引用</th><th>ops</th></tr></thead>
        <tbody>
          <tr v-for="m in rows" :key="m.id">
            <td><code>{{ m.mapping_key }}</code></td>
            <td>r{{ m.revision }}</td>
            <td><StatusTag :status="m.state" /></td>
            <td class="muted">{{ dictRefText(m.dictionary_refs) }}</td>
            <td class="muted">{{ (m.rules?.ops || []).length }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 新建映射 -->
    <div class="card">
      <b>新建映射（服务端 validate_mapping 校验；更正=新 revision）</b>
      <div class="row">
        <input v-model.trim="newKey" placeholder="mapping_key（如 pathology.index）"
               style="width: 240px" />
        <span class="muted small">target 是否表示嵌套路径以引擎真实语义为准（当前为平铺目标字段）。</span>
      </div>

      <div class="mode-bar">
        <button :class="{ active: viewMode === 'table' }" @click="switchToTable">表格模式</button>
        <button :class="{ active: viewMode === 'json' }" @click="switchToJson">高级 JSON</button>
        <span v-if="advancedOnly" class="adv-note">
          当前文档含表格不支持的结构（未知算子/非对象行）——高级模式编辑，
          不会重组丢配置。</span>
      </div>

      <!-- 表格模式 -->
      <template v-if="viewMode === 'table' && !advancedOnly">
        <table class="mini-table rules-table">
          <thead>
            <tr><th style="width:110px">算子</th><th>target</th>
              <th>参数（按算子）</th>
              <th style="width:130px">操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in tableRows" :key="r._idx">
              <td>
                <select v-model="r.op" @change="onOpChanged(r)">
                  <option v-for="o in RULE_OPS" :key="o.op" :value="o.op">{{ o.label }}</option>
                </select>
              </td>
              <td><input v-model.trim="r.target" /></td>
              <td>
                <div v-for="c in paramCols(r.op)" :key="c.key" class="param-row">
                  <span class="param-name">{{ c.label }}<span v-if="c.required" class="req">*</span></span>
                  <input v-model="r.params[c.key]"
                         :type="c.type === 'number' ? 'number' : 'text'"
                         :placeholder="c.type === 'string-list' ? 'a,b,c' : ''" />
                </div>
                <span v-if="!paramCols(r.op).length" class="muted small">（无参数）</span>
              </td>
              <td class="op-cell">
                <button class="ghost mini" :disabled="i === 0"
                        @click="moveRow(i, -1)">↑</button>
                <button class="ghost mini" :disabled="i === tableRows.length - 1"
                        @click="moveRow(i, 1)">↓</button>
                <button class="ghost mini" @click="tableRows.splice(i, 1)">删</button>
                <span v-if="r._extra" class="muted small"
                      :title="JSON.stringify(r._extra)">+扩展键</span>
              </td>
            </tr>
          </tbody>
        </table>
        <button class="ghost" @click="addRow">+ 加一条规则</button>
        <p v-if="rowErrors.length" class="err">
          <span v-for="(e, i) in rowErrors" :key="i">第 {{ e.row + 1 }} 行：{{ e.message }}<br /></span>
        </p>
      </template>

      <!-- 高级 JSON（无损；advancedOnly 时唯一编辑入口） -->
      <textarea v-if="viewMode === 'json' || advancedOnly"
                v-model="rulesJson" rows="10" spellcheck="false"
                style="width:100%"></textarea>
      <p v-if="jsonError" class="err">{{ jsonError }}</p>

      <!-- 字典引用：显式 namespace+version（批准状态可见；无批准显示原因） -->
      <label class="sec">字典引用（显式版本——禁止"最新"漂移引用）</label>
      <div v-for="(d, i) in dictRefs" :key="i" class="row">
        <select v-model="d.namespace">
          <option value="">— 选择字典 —</option>
          <option v-for="dic in dicts" :key="dic.id" :value="dic.namespace"
                  :disabled="!dic.approved_by">
            {{ dic.namespace }}@{{ dic.version }}{{ dic.approved_by ? '（已批准）' : '（未批准）' }}
          </option>
        </select>
        <input v-model.trim="d.version" placeholder="version（显式）" style="width:140px" />
        <button class="ghost mini" @click="dictRefs.splice(i, 1)">删</button>
        <span v-if="dictStatus(d)" class="muted small">{{ dictStatus(d) }}</span>
      </div>
      <button class="ghost mini" @click="dictRefs.push({ namespace: '', version: '' })">
        + 引用字典</button>

      <div class="row" style="margin-top:8px">
        <button :disabled="busyCreate" @click="createMapping">
          {{ busyCreate ? '创建中…' : '创建映射' }}</button>
        <span v-if="createErr" class="err">{{ createErr }}</span>
      </div>
    </div>

    <!-- 模拟面板：输入/输出/逐字段来源并列；服务端试算 -->
    <div class="card">
      <b>模拟（POST /api/v1/mappings/{key}/simulate；≤20 条；服务端试算非前端伪造）</b>
      <div class="row">
        <select v-model="simKey">
          <option value="">— 选择映射 —</option>
          <option v-for="m in rows" :key="m.id" :value="m.mapping_key">
            {{ m.mapping_key }} r{{ m.revision }}</option>
        </select>
        <button class="ghost" @click="fillSynthetic">填入合成示例（非真实患者数据）</button>
        <button :disabled="busySim || !simKey" @click="simulate">
          {{ busySim ? '模拟中…' : '模拟' }}</button>
      </div>
      <textarea v-model="recordsJson" rows="5" spellcheck="false" style="width:100%"
                placeholder='[{"org":"H","amount":"12.3","kind":"住院"}]'></textarea>
      <div class="sim-grid" v-if="simResult">
        <div>
          <b>输入</b>
          <JsonViewer :data="simInput" />
        </div>
        <div>
          <b>输出（逐字段来源/字典命中/错误）</b>
          <table class="mini-table">
            <thead><tr><th>行</th><th>字段</th><th>值</th><th>来源字段</th>
              <th>字典</th><th>错误</th></tr></thead>
            <tbody>
              <tr v-for="cell in simRows" :key="cell.row + cell.field">
                <td>{{ cell.row }}</td><td><code>{{ cell.field }}</code></td>
                <td><code>{{ cell.value }}</code></td>
                <td class="muted">{{ cell.sourceField ?? '—' }}</td>
                <td>{{ cell.dictionaryApplied ? '是' : '—' }}</td>
                <td :class="cell.error ? 'err' : 'muted'">{{ cell.error || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <EmptyState v-if="!simRows.length" text="该输入无输出字段" />
        </div>
      </div>
      <p v-if="simErr" class="err">模拟失败：{{ simErr }}（上次输入已保留）</p>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import JsonViewer from '../components/JsonViewer.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import { toast } from '../components/Toast'
import { RULE_OPS, parseRulesJson, rulesToRows, rowsToRules, validateRows,
  normalizeDictRefs } from '../utils/mappingConvert'
import type { MappingRow } from '../utils/mappingConvert'

const rows = ref<any[]>([])
const rowsLoading = ref(true)
const rowsError = ref('')
const dicts = ref<any[]>([])

const newKey = ref('')
const viewMode = ref<'table' | 'json'>('table')
const advancedOnly = ref(false)
const rulesJson = ref(JSON.stringify({
  ops: [
    { target: 'org', op: 'field', source_field: 'org' },
    { target: 'amount', op: 'decimal_scale', source_field: 'amount', places: 2 },
    { target: 'kind', op: 'dict_map', source_field: 'kind',
      dictionary: 'encounter_kind', default: '9' },
  ]
}, null, 2))
const tableRows = ref<MappingRow[]>([])
const jsonError = ref('')
const dictRefs = reactive<{ namespace: string, version: string }[]>(
  [{ namespace: 'encounter_kind', version: 'demo-v2' }])
const busyCreate = ref(false)
const createErr = ref('')

const simKey = ref('')
const recordsJson = ref('[{"org":"H","amount":"12.3","kind":"住院"}]')
const simErr = ref('')
const simResult = ref<any[] | null>(null)
const simInput = computed(() => {
  try { return JSON.parse(recordsJson.value) } catch { return recordsJson.value }
})
const busySim = ref(false)
const simRows = computed(() => {
  const out: any[] = []
  for (const rec of simResult.value || []) {
    for (const [field, f] of Object.entries(rec.fields || {})) {
      out.push({
        row: rec.row, field,
        value: (f as any).value,
        sourceField: (f as any).source_field,
        dictionaryApplied: (f as any).dictionary_applied,
        error: (f as any).error ? (f as any).error.message : null,
      })
    }
  }
  return out
})

function dictRefText(refs: any[]): string {
  return (refs || []).map((d: any) =>
    `${d.dictionary || d.namespace}@${d.version}`).join('；') || '—'
}
function dictStatus(d: { namespace: string, version: string }): string {
  if (!d.namespace) return ''
  const dic = dicts.value.find(x => x.namespace === d.namespace)
  if (!dic) return '（未找到该字典）'
  if (!dic.approved_by) return '（未批准——发布链会拒绝，先去医院配置包页批准）'
  if (String(dic.version) !== String(d.version)) {
    return `（注意：该字典现有版本 ${dic.version}）`
  }
  return `（已批准 by ${dic.approved_by}）`
}

function paramCols(op: string) {
  return RULE_OPS.find(o => o.op === op)?.params ?? []
}
function onOpChanged(r: MappingRow): void {
  r.params = {}
}
function addRow(): void {
  tableRows.value.push({ _idx: tableRows.value.length, op: 'field',
    target: '', params: { source_field: '' }, _extra: null })
}
function moveRow(i: number, dir: number): void {
  const j = i + dir
  if (j < 0 || j >= tableRows.value.length) return
  const arr = tableRows.value
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
}
const rowErrors = computed(() => validateRows(tableRows.value))

function switchToTable(): void {
  const parsed = parseRulesJson(rulesJson.value)
  if (!parsed.ok) { jsonError.value = parsed.error ?? 'JSON 非法'; return }
  jsonError.value = ''
  if (parsed.advancedOnly) {
    advancedOnly.value = true
    toast.error('文档含表格不支持的结构：保留高级模式（不会重组丢配置）')
    return
  }
  advancedOnly.value = false
  const r = rulesToRows(JSON.parse(rulesJson.value))
  if (r.rows) tableRows.value = r.rows
  viewMode.value = 'table'
}
function switchToJson(): void {
  if (viewMode.value === 'table' && !advancedOnly) {
    // 表格→JSON 无损（含 _extra 扩展键并回；行序保持）
    const rules = rowsToRules(tableRows.value)
    rulesJson.value = JSON.stringify(rules, null, 2)
  }
  viewMode.value = 'json'
}

function fillSynthetic(): void {
  // 明确标识的合成示例（不读真实患者记录）
  recordsJson.value = JSON.stringify(
    [{ org: '合成H', amount: '12.3', kind: '住院', sample_date: '2026-01-01' }],
    null, 1)
  toast.info('已填入合成示例（非真实患者数据）')
}

async function load(): Promise<void> {
  rowsLoading.value = true
  rowsError.value = ''
  try {
    rows.value = await api.coreGet('/api/v1/mappings')
    if (!simKey.value && rows.value.length) simKey.value = rows.value[0].mapping_key
  } catch (e: any) {
    rowsError.value = String(e?.message ?? e)     // 载入失败明确显示（不再卡加载中）
  } finally { rowsLoading.value = false }
}

async function createMapping(): Promise<void> {
  createErr.value = ''
  let rulesValue: unknown
  if (viewMode.value === 'table' && !advancedOnly) {
    const errs = rowErrors.value
    if (errs.length) {
      createErr.value = `行校验未过：${errs[0].message}（共 ${errs.length} 项）`
      return
    }
    rulesValue = rowsToRules(tableRows.value)
  } else {
    const parsed = parseRulesJson(rulesJson.value)
    if (!parsed.ok) { createErr.value = parsed.error ?? 'JSON 非法'; return }
    rulesValue = parsed.value
  }
  const refs = normalizeDictRefs(dictRefs)
  if (!refs.ok) { createErr.value = refs.error ?? '字典引用非法'; return }
  if (!newKey.value) { createErr.value = 'mapping_key 必填'; return }
  busyCreate.value = true
  try {
    await api.corePost('/api/v1/mappings', {
      mapping_key: newKey.value,
      rules: rulesValue,
      dictionary_refs: refs.refs,
    })
    toast.success(`映射 ${newKey.value} 已创建`)
    await load()
  } catch (e: any) {
    createErr.value = `创建失败：${e?.data?.error?.code || ''} ${e.message}（输入已保留，可修正后重试）`
  } finally { busyCreate.value = false }
}

async function simulate(): Promise<void> {
  simErr.value = ''
  let records: unknown
  try {
    records = JSON.parse(recordsJson.value)
    if (!Array.isArray(records) || !records.length) {
      throw new Error('records 须为非空数组（≤20 条）')
    }
  } catch (e: any) {
    simErr.value = `输入非法：${e.message}`     // 失败不清空上次输入
    return
  }
  busySim.value = true
  try {
    const d = await api.corePost(
      `/api/v1/mappings/${encodeURIComponent(simKey.value)}/simulate`,
      { records })
    simResult.value = d.records
  } catch (e: any) {
    simErr.value = `${e?.data?.error?.code || ''} ${e.message}`
  } finally { busySim.value = false }
}

onMounted(async () => {
  load()
  try { dicts.value = await api.coreGet('/api/v1/dictionaries') }
  catch { dicts.value = [] }                    // 下拉留空并显示原因
})
</script>

<style scoped>
.hint-bar { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af;
  padding: 8px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 12px; }
.card { background: var(--c-surface, #fff); border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px; padding: 12px; margin-bottom: 14px; }
.row { display: flex; gap: 8px; align-items: center; margin: 6px 0; flex-wrap: wrap; }
.mode-bar { display: flex; gap: 8px; align-items: center; margin: 10px 0 6px; }
.mode-bar .active { font-weight: 600; }
.adv-note { color: #92400e; font-size: 12px; }
.rules-table select, .rules-table input { width: 100%; box-sizing: border-box; }
.rules-table td { vertical-align: top; }
.param-row { display: flex; gap: 6px; align-items: center; margin: 2px 0; }
.param-name { font-size: 12px; color: var(--text-muted, #6b7280); width: 110px;
  flex-shrink: 0; }
.op-cell { white-space: nowrap; }
.mini-table { width: 100%; font-size: 13px; border-collapse: collapse; margin: 6px 0; }
.mini-table th, .mini-table td { text-align: left; padding: 5px 6px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
button.mini { padding: 0 6px; font-size: 11px; }
.req { color: var(--c-danger, #b91c1c); }
.sec { display: block; font-size: 13px; font-weight: 600; margin: 10px 0 4px; }
.sim-grid { display: grid; grid-template-columns: 1fr 1.4fr; gap: 12px; margin-top: 8px; }
@media (max-width: 900px) { .sim-grid { grid-template-columns: 1fr; } }
.err { color: var(--c-danger, #b91c1c); font-size: 12px; }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
</style>
