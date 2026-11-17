<!-- LT-02 改造：DataTable + TimeText + JsonViewer 抽屉看详情。
     API 不变：GET /api/v1/audit?limit=200。 -->
<template>
  <div>
    <PageHeader title="系统审计" subtitle="只追加；界面没有修改/删除审计入口；人工操作带理由与前后版本" />
    <DataTable :columns="cols" :rows="rows ?? []" :loading="loading" :error="error"
               empty-text="暂无审计记录" row-key="id" @retry="load"
               @row-click="r => openDetail(r)">
      <template #col-at="{ row }"><TimeText :value="row.at" /></template>
      <template #col-reason="{ row }">{{ row.reason || '—' }}</template>
    </DataTable>
    <Drawer v-model:open="detailOpen" title="审计详情" width="560px">
      <JsonViewer v-if="detailRow" :data="detailRow" :default-depth="2" />
    </Drawer>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import Drawer from '../components/Drawer.vue'
import JsonViewer from '../components/JsonViewer.vue'
import TimeText from '../components/TimeText.vue'

const rows = ref<any[] | null>(null), loading = ref(true), error = ref('')
const detailOpen = ref(false), detailRow = ref<any>(null)
const cols = [
  { key: 'at', title: '时间', width: '170px', formatter: () => '', slot: 'col-at' },
  { key: 'actor', title: '操作者' },
  { key: 'action', title: '动作' },
  { key: 'target', title: '对象' },
  { key: 'reason', title: '理由', slot: 'col-reason' },
]
function openDetail(r: any) { detailRow.value = r; detailOpen.value = true }
async function load() {
  loading.value = true; error.value = ''
  try { rows.value = await api.coreGet('/api/v1/audit?limit=200') }
  catch (x: any) { error.value = x.message || '加载失败' }
  finally { loading.value = false }
}
onMounted(load)
</script>
