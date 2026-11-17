<!-- LT-02 改造：PageHeader + DataTable + StatusTag + TimeText + Toast。
     API 与参数不变：GET /api/v1/reconciliations、POST /api/v1/reconciliations {destination:'mock-push'}。 -->
<template>
  <div>
    <PageHeader title="对账"
      subtitle="本地确认 vs 回执差异；可恢复项提交补偿，不盲目全量重传">
      <button :disabled="runBusy" @click="run">生成差异快照（mock-push）</button>
    </PageHeader>
    <DataTable :columns="cols" :rows="rows ?? []" :loading="loading" :error="error"
               empty-text="暂无对账记录" row-key="id" @retry="load">
      <template #col-id="{ row }">{{ (row.id || '').slice(0, 8) }}</template>
      <template #col-state="{ row }"><StatusTag :state="row.state" /></template>
      <template #col-time="{ row }"><TimeText :value="row.created_at" /></template>
    </DataTable>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import StatusTag from '../components/StatusTag.vue'
import TimeText from '../components/TimeText.vue'
import { toast } from '../components/Toast'

const rows = ref<any[] | null>(null), loading = ref(true), error = ref('')
const runBusy = ref(false)
const cols = [
  { key: 'id', title: '对账', width: '100px', slot: 'col-id' },
  { key: 'state', title: '状态', width: '110px', slot: 'col-state' },
  { key: 'summary', title: '检查', formatter: (r: any) => String(r.summary?.checked ?? '—') },
  { key: 'summary2', title: '差异', formatter: (r: any) => String(r.summary?.differences ?? '—') },
  { key: 'created_at', title: '时间', slot: 'col-time' },
]
async function load() {
  loading.value = true; error.value = ''
  try { rows.value = await api.coreGet('/api/v1/reconciliations') }
  catch (x: any) { error.value = x.message || '加载失败' }
  finally { loading.value = false }
}
async function run() {
  runBusy.value = true
  try {
    await api.corePost('/api/v1/reconciliations', { destination: 'mock-push' })
    toast.success('差异快照已生成')
    await load()
  } catch (x: any) { toast.error(x.message || '对账执行失败') }
  finally { runBusy.value = false }
}
onMounted(load)
</script>
