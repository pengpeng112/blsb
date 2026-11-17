<!-- RT-05 U09：质量队列（Toast 提示；规则码中文说明带原 code；单条/批量重评
     loading 与部分失败真实展示；重评不跳过质量等待——服务端语义）。 -->
<template>
  <div>
    <PageHeader title="质量队列" subtitle="每条问题显示卡在哪、来源、如何修复；复评不修改已冻结历史" />
    <FilterBar :busy="loading" @search="load" @reset="rule = ''; load()">
      <label>规则
        <select v-model="rule">
          <option value="">全部规则</option>
          <option v-for="r in RULE_LABELS" :key="r.code" :value="r.code">
            {{ r.code }}（{{ r.label }}）</option>
        </select>
      </label>
    </FilterBar>
    <div class="toolbar">
      <button class="ghost" :disabled="!selected.length || batchBusy"
              @click="batchReevaluate">
        {{ batchBusy ? `批量重评中（${batchDone}/${selected.length}）…` : `批量重评（已选 ${selected.length}）` }}</button>
      <span class="muted small">部分失败将逐条如实展示，不静默。</span>
    </div>
    <LoadingBlock v-if="loading" />
    <ErrorState v-else-if="loadErr" :message="loadErr" @retry="load" />
    <EmptyState v-else-if="!rows.length" text="暂无质量问题（当前筛选）" />
    <table v-else class="mini-table">
      <thead><tr><th></th><th>规则</th><th>阻断范围</th><th>对象</th>
        <th>说明</th><th>评估时间</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="i in rows" :key="i.id">
          <td><input type="checkbox" v-model="selected" :value="i.id" /></td>
          <td>
            <b>{{ i.rule_code }}</b>
            <span class="muted small">{{ ruleLabel(i.rule_code) }}</span>
          </td>
          <td><StatusTag :status="i.severity === 'BLOCKING' ? 'ERROR' : 'WARN'"
                         :map="{ ERROR: '阻断', WARN: '观察' }" /></td>
          <td class="muted">{{ i.entity_ref }}</td>
          <td>{{ i.context?.detail }}</td>
          <td class="muted"><TimeText :value="i.evaluated_at" /></td>
          <td><StatusTag :status="i.state" /></td>
          <td>
            <button class="ghost" :disabled="reevaluating[i.id]"
                    @click="reevaluate(i)">
              {{ reevaluating[i.id] ? '评估中…' : '重新评估' }}</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import FilterBar from '../components/FilterBar.vue'
import StatusTag from '../components/StatusTag.vue'
import TimeText from '../components/TimeText.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import { toast } from '../components/Toast'

// 规则码中文说明（未知规则如实显示原 code，不乱翻译）
const RULE_LABELS = [
  { code: 'QF-DATE-FUTURE', label: '日期在未来' },
  { code: 'QF-DATE-BEFORE-BIRTH', label: '日期早于出生' },
  { code: 'QF-DATE-ORDER', label: '日期顺序矛盾' },
  { code: 'QF-TIME-SEMANTICS', label: '时间语义异常' },
  { code: 'QF-ID-TYPE', label: '标识类型不符' },
  { code: 'QF-ID-CONFLICT', label: '标识冲突' },
  { code: 'QF-CATEGORY', label: '类别值异常' },
  { code: 'QF-ORG', label: '机构编码异常' },
  { code: 'QF-STATUS', label: '状态值异常' },
  { code: 'QF-SOURCE-KEY', label: '来源键缺失' },
  { code: 'QF-ENCOUNTER', label: '就诊关联异常' },
  { code: 'QF-COMPLETENESS', label: '完整性不足' },
]
function ruleLabel(code: string): string {
  return RULE_LABELS.find(r => r.code === code)?.label ?? '（未登记说明）'
}

const rule = ref('')
const rows = ref<any[]>([])
const loading = ref(true)
const loadErr = ref('')
const reevaluating = reactive<Record<string, boolean>>({})
const selected = ref<string[]>([])
const batchBusy = ref(false)
const batchDone = ref(0)

async function load(): Promise<void> {
  loading.value = true
  loadErr.value = ''
  try {
    const d = await api.coreGet('/api/v1/quality-issues' +
      (rule.value ? '?rule_code=' + rule.value : ''))
    rows.value = Array.isArray(d) ? d : (d.items ?? [])
    selected.value = []
  } catch (e: any) { loadErr.value = String(e?.message ?? e) }
  finally { loading.value = false }
}
async function reevaluate(i: any): Promise<void> {
  reevaluating[i.id] = true
  try {
    await api.corePost(`/api/v1/quality-issues/${i.id}/reevaluate`, {})
    toast.success(`已触发重评估：${i.rule_code}`)
    await load()
  } catch (e: any) {
    toast.error(`重评失败：${e.message}`)       // 失败如实
  } finally { reevaluating[i.id] = false }
}
async function batchReevaluate(): Promise<void> {
  batchBusy.value = true
  batchDone.value = 0
  const failures: string[] = []
  for (const id of selected.value) {
    try {
      await api.corePost(`/api/v1/quality-issues/${id}/reevaluate`, {})
    } catch (e: any) {
      failures.push(`${id.slice(0, 8)}：${e.message}`)   // 部分失败逐条保留
    }
    batchDone.value++
  }
  batchBusy.value = false
  if (failures.length) toast.error(`批量重评部分失败（${failures.length}/${selected.value.length}）：${failures[0]}…`)
  else toast.success(`批量重评完成（${selected.value.length} 条）`)
  await load()
}
onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
.mini-table { width: 100%; font-size: 13px; border-collapse: collapse; margin: 6px 0; }
.mini-table th, .mini-table td { text-align: left; padding: 5px 6px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
</style>
