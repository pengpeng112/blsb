<!-- RT-05 U09 Cases：状态筛选、患者/就诊分列、宽抽屉展示玻片→扫描版本树
     与未报原因。0/1/N、冻结 revision、前导零保持（原值字符串展示）；
     详情失败不清空主表。 -->
<template>
  <div>
    <PageHeader title="病理工作台"
      subtitle="分列状态：文件落盘 / 玻片关联 / 临床就绪 / 目标确认（0/1/N 以后端为准）" />
    <FilterBar :busy="loading" @search="load" @reset="q = ''; state = ''; load()">
      <label>病理号（原值，保留前导零）
        <input v-model.trim="q" placeholder="如 0000123" />
      </label>
      <label>状态
        <select v-model="state">
          <option value="">全部</option>
          <option v-for="s in STATES" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
    </FilterBar>
    <LoadingBlock v-if="loading" />
    <ErrorState v-else-if="loadErr" :message="loadErr" @retry="load" />
    <EmptyState v-else-if="!rows.length" text="暂无病例（当前筛选）" />
    <table v-else class="mini-table">
      <thead><tr><th>病理号</th><th>命名空间</th><th>患者</th><th>就诊</th>
        <th>文件</th><th>玻片</th><th>临床</th><th>扫描/玻片</th>
        <th>未决问题</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="c in rows" :key="c.id">
          <td><code>{{ c.pathology_no }}</code></td>
          <td class="muted">{{ c.namespace }}</td>
          <!-- 患者/就诊分列（各自独立状态；不合并语义） -->
          <td><span class="muted small">见详情（脱敏展示）</span></td>
          <td><span class="muted small">{{ c.clinically_ready ? '已关联' : '等待' }}</span></td>
          <td><StatusTag :status="c.files_complete ? 'OK' : 'WARN'"
                         :map="{ OK: '齐', WARN: '缺' }" /></td>
          <td><StatusTag :status="c.slides_linked ? 'OK' : 'WARN'"
                         :map="{ OK: '齐', WARN: '缺' }" /></td>
          <td><StatusTag :status="c.clinically_ready ? 'OK' : 'WARN'"
                         :map="{ OK: '就绪', WARN: '等待' }" /></td>
          <td>{{ c.scans }}/{{ c.slides }}</td>
          <td>{{ c.open_quality_issues || '' }}</td>
          <td><button class="ghost" @click="detail(c)">详情</button></td>
        </tr>
      </tbody>
    </table>

    <!-- 宽抽屉：玻片→扫描版本树 + 未报原因 -->
    <Drawer :open="!!sel" :title="`病例 ${sel?.case?.pathology_no ?? ''}`"
            width="900px" @close="sel = null">
      <template v-if="sel">
        <p v-if="detailErr" class="err">{{ detailErr }}（主表数据保留）</p>
        <h4>患者 / 就诊（分列；脱敏）</h4>
        <dl class="kv">
          <dt>患者</dt><dd>{{ sel.patient?.masked || '未关联' }}
            <span class="muted small">身份状态
              {{ sel.patient?.identity?.patient_state || '—' }}</span></dd>
          <dt>就诊</dt>
          <dd>
            <template v-if="sel.encounters?.length">
              <span v-for="e in sel.encounters" :key="e.visit_id" class="enc-tag">
                V{{ e.visit_id }} <StatusTag :status="e.state" /></span>
            </template>
            <span v-else class="muted small">等待（无已确认 VISIT 时不选最新——0/1/N 后端为准）</span>
          </dd>
          <dt>冻结 revision</dt>
          <dd>{{ sel.case?.state || '—' }}</dd>
        </dl>
        <h4>玻片与扫描版本树（前导零原值）</h4>
        <table class="mini-table">
          <thead><tr><th>玻片条码(原值)</th><th>扫描ID</th><th>版本</th><th>有效</th><th>资产</th></tr></thead>
          <tbody>
            <template v-for="s in sel.slides" :key="s.id">
              <template v-if="s.scans.length">
                <tr v-for="(sc, i) in s.scans" :key="sc.scan_id">
                  <td v-if="i === 0" :rowspan="s.scans.length" style="font-weight:600">
                    <code>{{ s.barcode_raw }}</code></td>
                  <td>{{ sc.source_scan_id }} r{{ sc.revision }}</td>
                  <td><StatusTag :status="sc.effective ? 'OK' : '历史'"
                                 :map="{ OK: '有效', 历史: '历史' }" /></td>
                  <td class="muted">{{ sc.assets.map((a: any) => a.role + ':' + a.state).join(', ') }}</td>
                </tr>
              </template>
              <tr v-else>
                <td style="font-weight:600"><code>{{ s.barcode_raw }}</code></td>
                <td colspan="3" class="muted">无已归集扫描（预期清单已含此片，等待扫描事件——未报原因）</td>
              </tr>
            </template>
          </tbody>
        </table>
        <h4>索引候选</h4>
        <EmptyState v-if="!sel.index_records?.length" text="暂无（质量门禁未过或字段等待）" />
        <p v-for="i in sel.index_records" :key="i.id">
          <code>{{ i.display_value }}</code> <StatusTag :status="i.status" />
        </p>
        <h4>包版本 / 提交 / 发布（未报原因对照）</h4>
        <table class="mini-table">
          <thead><tr><th>修订</th><th>状态</th><th>提交</th><th>发布</th></tr></thead>
          <tbody>
            <tr v-for="r in sel.package_revisions" :key="r.revision">
              <td>r{{ r.revision }}</td><td>{{ r.state }}</td>
              <td class="muted">{{ r.submissions.map((s: any) => s.state).join(',') || '—' }}</td>
              <td class="muted">{{ r.publications.map((p: any) => p.state).join(',') || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <h4>质量问题</h4>
        <EmptyState v-if="!sel.quality_issues?.length" text="无质量问题" />
        <table v-else class="mini-table">
          <thead><tr><th>规则</th><th>范围</th><th>说明</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="i in sel.quality_issues" :key="i.id">
              <td>{{ i.rule_code }}</td><td>{{ i.blocking_scope }}</td>
              <td>{{ i.detail }}</td>
              <td><StatusTag :status="i.state" /></td>
            </tr>
          </tbody>
        </table>
      </template>
    </Drawer>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import FilterBar from '../components/FilterBar.vue'
import StatusTag from '../components/StatusTag.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import Drawer from '../components/Drawer.vue'
import { toast } from '../components/Toast'

const STATES = ['PENDING', 'RESOLVED', 'WAITING', 'NEEDS_REVIEW']
const q = ref('')
const state = ref('')
const rows = ref<any[]>([])
const loading = ref(true)
const loadErr = ref('')
const sel = ref<any>(null)
const detailErr = ref('')

async function load(): Promise<void> {
  loading.value = true
  loadErr.value = ''
  try {
    const params = new URLSearchParams()
    if (q.value) params.set('q', q.value)
    if (state.value) params.set('state', state.value)
    const d = await api.coreGet('/api/v1/pathology/cases' +
      (params.toString() ? '?' + params.toString() : ''))
    rows.value = Array.isArray(d) ? d : []
  } catch (e: any) { loadErr.value = String(e?.message ?? e) }
  finally { loading.value = false }
}
async function detail(c: any): Promise<void> {
  detailErr.value = ''
  try {
    sel.value = await api.coreGet('/api/v1/pathology/cases/' + c.id)
  } catch (e: any) {
    // 详情失败：主表数据保留（不清空），只提示
    detailErr.value = String(e?.message ?? e)
    toast.error(`详情加载失败：${detailErr.value}`)
  }
}
onMounted(load)
</script>

<style scoped>
.mini-table { width: 100%; font-size: 13px; border-collapse: collapse; margin: 6px 0; }
.mini-table th, .mini-table td { text-align: left; padding: 5px 6px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
.kv { display: grid; grid-template-columns: 110px 1fr; gap: 4px 10px;
  font-size: 13px; margin: 8px 0; }
.kv dt { color: var(--text-muted, #6b7280); }
.enc-tag { margin-right: 8px; display: inline-block; }
.err { color: var(--c-danger, #b91c1c); font-size: 12px; }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
h4 { margin: 14px 0 4px; }
</style>
