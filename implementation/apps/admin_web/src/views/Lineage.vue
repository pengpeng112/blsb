<!-- RT-05 U09 Lineage：配置映射/实测求值/设计规范三栏对照。
     缺数据标"未提供"，禁止从静态设计冒充实测结果。 -->
<template>
  <div>
    <PageHeader title="字段血缘" subtitle="configured（真实映射）/ evaluated（病例实测）/ design（设计基线）三栏对照" />
    <div class="hint-bar">
      对照原则：<b>configured</b>=当前映射修订的真实血缘；
      <b>evaluated</b>=病例实际生效的规则版本（实测）；
      <b>design</b>=设计基线文档（非实测）。缺任一侧时该栏标"未提供"，
      不以设计文档冒充实测。
    </div>
    <LoadingBlock v-if="loading" />
    <ErrorState v-else-if="loadErr" :message="loadErr" @retry="load" />
    <div v-else class="three-col">
      <!-- configured -->
      <div class="card">
        <b>configured（当前映射修订）</b>
        <EmptyState v-if="!configured.length" text="未提供（暂无映射修订——先在字段映射页创建）" />
        <template v-else>
          <div v-for="m in configured" :key="m.mapping_key" class="seg">
            <b><code>{{ m.mapping_key }}</code></b>
            <span class="muted small"> rev {{ m.revision }} · {{ m.state }}</span>
            <table v-if="m.fields.length" class="mini-table">
              <thead><tr><th>目标</th><th>来源</th><th>转换</th><th>字典</th></tr></thead>
              <tbody>
                <tr v-for="f in m.fields" :key="f.target + f.source_field">
                  <td><code>{{ f.target }}</code></td>
                  <td><code>{{ f.source_field || '—' }}</code></td>
                  <td>{{ f.op || '—' }}</td>
                  <td>{{ f.dictionary || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </div>

      <!-- evaluated -->
      <div class="card">
        <b>evaluated（按病例实测）</b>
        <div class="row">
          <input v-model.trim="caseId" placeholder="case id" style="width: 220px" />
          <button class="ghost" :disabled="!caseId || evalLoading" @click="loadEvaluated">
            {{ evalLoading ? '查询中…' : '查询' }}</button>
        </div>
        <EmptyState v-if="!evaluated" text="未提供（输入病例 ID 查询实测）" />
        <template v-else-if="evaluated.found">
          <dl class="kv">
            <dt>冻结包</dt>
            <dd>{{ evaluated.package_id ? `${evaluated.package_id.slice(0, 8)} r${evaluated.package_revision}（${evaluated.package_state}）` : '未提供' }}</dd>
            <dt>索引状态</dt><dd>{{ evaluated.index_status || '未提供' }}</dd>
          </dl>
          <EmptyState v-if="!ruleVersionCount" text="未提供（该病例无规则/字典版本记录）" />
          <table v-else class="mini-table">
            <thead><tr><th>规则</th><th>实际版本</th></tr></thead>
            <tbody>
              <tr v-for="(v, k) in evaluated.rule_versions" :key="String(k)">
                <td><code>{{ k }}</code></td><td>{{ v }}</td>
              </tr>
              <tr v-for="(v, k) in evaluated.dictionary_revisions" :key="'d-' + k">
                <td><code>dict:{{ k }}</code></td><td>{{ v }}</td>
              </tr>
            </tbody>
          </table>
        </template>
        <EmptyState v-else text="未找到该病例（或病理插件未启用）" />
      </div>

      <!-- design -->
      <div class="card">
        <b>design（设计基线；文档口径，非实测）</b>
        <EmptyState v-if="!design || !Object.keys(design).length" text="未提供" />
        <table v-else class="mini-table">
          <thead><tr><th>内部字段</th><th>来源与处理约束</th></tr></thead>
          <tbody>
            <tr v-for="(v, k) in design" :key="String(k)">
              <td><code>{{ k }}</code></td><td>{{ v }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'

const loading = ref(true)
const loadErr = ref('')
const design = ref<Record<string, string> | null>(null)
const configured = ref<any[]>([])
const evaluated = ref<any>(null)
const caseId = ref('')
const evalLoading = ref(false)

const ruleVersionCount = computed(() =>
  Object.keys(evaluated.value?.rule_versions ?? {}).length +
  Object.keys(evaluated.value?.dictionary_revisions ?? {}).length)

async function fetchLineage(caseIdQ = ''): Promise<void> {
  const d = await api.coreGet('/api/v1/field-lineage' + caseIdQ)
  design.value = d.design ?? null
  configured.value = d.configured || []
  return d.evaluated ?? null
}
async function load(): Promise<void> {
  loading.value = true
  loadErr.value = ''
  try {
    evaluated.value = await fetchLineage()
    if (!caseId.value) caseId.value = localStorage.getItem('lineage-case') || ''
  } catch (e: any) { loadErr.value = String(e?.message ?? e) }
  finally { loading.value = false }
}
async function loadEvaluated(): Promise<void> {
  evalLoading.value = true
  try {
    const ev = await fetchLineage('?case_id=' + encodeURIComponent(caseId.value))
    evaluated.value = ev
    localStorage.setItem('lineage-case', caseId.value)
  } catch (e: any) { /* 查询失败保留上次 evaluated */ }
  finally { evalLoading.value = false }
}
onMounted(load)
</script>

<style scoped>
.hint-bar { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af;
  padding: 8px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 12px; }
.three-col { display: grid; grid-template-columns: 1.2fr 1fr 1fr; gap: 12px; }
@media (max-width: 1100px) { .three-col { grid-template-columns: 1fr; } }
.card { background: var(--c-surface, #fff); border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px; padding: 12px; }
.seg { margin-bottom: 10px; }
.row { display: flex; gap: 8px; margin: 6px 0; }
.kv { display: grid; grid-template-columns: 90px 1fr; gap: 4px 10px;
  font-size: 13px; margin: 6px 0; }
.kv dt { color: var(--text-muted, #6b7280); }
.mini-table { width: 100%; font-size: 12px; border-collapse: collapse; margin: 6px 0; }
.mini-table th, .mini-table td { text-align: left; padding: 4px 5px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
</style>
