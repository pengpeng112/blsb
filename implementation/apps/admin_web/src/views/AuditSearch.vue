<!-- LT-06B P3 审计检索与导出。先筛后限（全部条件后端执行）；详情 Drawer 展示
     old/new refs；导出走后端 CSV。
     RT-01：① 本页占用正位路由 /audit（/audit2 重定向过来，侧栏只有一个审计入口）；
     ② 导出改为带 Bearer 的 fetch + Blob 下载（原生 <a download> 不带鉴权头必 401）；
     ③ datetime-local 按院内东八区换算为 UTC 再发后端（此前偏移 8 小时）；
     ④ 时间统一院内时区展示；错误用 toast，不再 window.alert。 -->
<template>
  <div>
    <PageHeader title="审计检索" subtitle="操作审计（可导出 CSV；导出动作本身被审计）">
      <template #actions>
        <button :disabled="!canExport || exporting" @click="exportCsv">
          {{ exporting ? '导出中…' : '导出 CSV' }}</button>
      </template>
    </PageHeader>
    <FilterBar :busy="loading" @search="reload" @reset="reset">
      <input v-model="f.actor" placeholder="操作人（前缀）" />
      <input v-model="f.action" placeholder="动作（精确）" />
      <input v-model="f.actionPrefix" placeholder="动作前缀" />
      <input v-model="f.target" placeholder="对象（前缀）" />
      <input v-model="f.atFrom" type="datetime-local" title="开始（院内时间，含）" />
      <input v-model="f.atTo" type="datetime-local" title="结束（院内时间，不含）" />
    </FilterBar>

    <DataTable :columns="cols" :rows="rows" :loading="loading" :error="error"
               @retry="reload" :has-more="!!cursor" :loading-more="more"
               @load-more="loadMore" @row-click="openDetail"
               empty-text="无匹配审计记录" />

    <Drawer :open="detailOpen" :title="detail?.action ?? '审计详情'"
            @close="closeDetail">
      <ErrorState v-if="detailError" :message="detailError" compact />
      <template v-else-if="detail">
        <div class="kv">
          <span class="muted">时间</span><TimeText :value="detail.at" />
          <span class="muted">操作人</span><span>{{ detail.actor }}</span>
          <span class="muted">对象</span><span style="word-break:break-all">{{ detail.target }}</span>
        </div>
        <div class="refs">
          <div><b>变更前</b><JsonViewer :data="detail.old_ref ?? null" /></div>
          <div><b>变更后</b><JsonViewer :data="detail.new_ref ?? null" /></div>
        </div>
      </template>
    </Drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { searchAudit, auditDetail, exportAuditCsv } from '../api/index'
import type { AuditRow } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import FilterBar from '../components/FilterBar.vue'
import DataTable from '../components/DataTable.vue'
import Drawer from '../components/Drawer.vue'
import JsonViewer from '../components/JsonViewer.vue'
import TimeText from '../components/TimeText.vue'
import ErrorState from '../components/ErrorState.vue'
import { toast } from '../components/Toast'
import { formatHospitalTime } from '../utils/time'
import { buildAuditParams } from '../utils/payloads'

const f = ref({ actor: '', action: '', actionPrefix: '', target: '',
  atFrom: '', atTo: '' })
const rows = ref<AuditRow[]>([])
const cursor = ref<string | null>(null)
const loading = ref(false)
const more = ref(false)
const error = ref('')
const detail = ref<any>(null)
const detailError = ref('')
const exporting = ref(false)

const detailOpen = computed(() => !!(detail.value || detailError.value))

const canExport = computed(() => {
  try {
    const r = JSON.parse(localStorage.getItem('blyb.roles') || '[]')
    return r.includes('platform_admin') || r.includes('auditor')
  } catch { return false }
})

const cols = [
  { key: 'at', title: '时间', width: '160px', formatter: (r: AuditRow) =>
    formatHospitalTime(r.at) },
  { key: 'actor', title: '操作人', width: '150px' },
  { key: 'action', title: '动作', width: '210px' },
  { key: 'target', title: '对象' },
  { key: 'reason', title: '理由', formatter: (r: AuditRow) => r.reason || '—' },
]

function apiParams(cursorV?: string) {
  return { ...buildAuditParams(f.value), cursor: cursorV, limit: 100 }
}

async function reload(): Promise<void> {
  cursor.value = null
  rows.value = []
  loading.value = true
  error.value = ''
  try {
    const r: any = await searchAudit(apiParams())
    const body = Array.isArray(r) ? { items: r, next_cursor: null } : r
    rows.value = body.items ?? []
    cursor.value = body.next_cursor
  } catch (e: any) { error.value = String(e?.message ?? e) }
  finally { loading.value = false }
}

async function loadMore(): Promise<void> {
  if (!cursor.value) return
  more.value = true
  try {
    const r: any = await searchAudit(apiParams(cursor.value))
    const body = Array.isArray(r) ? { items: r, next_cursor: null } : r
    rows.value.push(...(body.items ?? []))
    cursor.value = body.next_cursor
  } catch (e: any) { toast.error(`加载更多失败：${e?.message ?? e}`) }
  finally { more.value = false }
}

function reset(): void {
  f.value = { actor: '', action: '', actionPrefix: '', target: '',
    atFrom: '', atTo: '' }
  reload()
}

async function openDetail(row: AuditRow): Promise<void> {
  detail.value = null
  detailError.value = ''
  try { detail.value = await auditDetail(row.id) }
  catch (e: any) { detailError.value = String(e?.message ?? e) }
}
function closeDetail(): void {
  detail.value = null
  detailError.value = ''
}

async function exportCsv(): Promise<void> {
  exporting.value = true
  try {
    const blob = await exportAuditCsv(buildAuditParams(f.value))
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'audit-export.csv'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    toast.success('审计 CSV 已导出（本次导出已记审计）')
  } catch (e: any) {
    // 后端超 10000 行等校验失败返回中文原因，原样提示
    toast.error(`导出失败：${e?.message ?? e}`)
  } finally { exporting.value = false }
}
onMounted(reload)
</script>

<style scoped>
.kv { display: grid; grid-template-columns: 70px 1fr; gap: 4px 10px;
  font-size: 13px; margin-bottom: 10px; }
.refs { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 800px) { .refs { grid-template-columns: 1fr; } }
</style>
