<!-- LT-02 改造：DataTable + StatusTag + BytesText。API 不变：storage GET /api/v1/storage/volumes。 -->
<template>
  <div>
    <PageHeader title="存储管理" subtitle="容量预留=分片合计+合并副本；低水位停新会话不删源文件；暂存/对象/隔离目录由服务生成" />
    <DataTable :columns="cols" :rows="rows ?? []" :loading="loading" :error="error"
               empty-text="暂无存储卷" row-key="id" @retry="load">
      <template #col-state="{ row }"><StatusTag :state="row.state" /></template>
      <template #col-free="{ row }"><BytesText :value="row.free_bytes" /></template>
      <template #col-reserved="{ row }">{{ mb(row.reserved_bytes) }}</template>
      <template #col-margin="{ row }">{{ mb(row.safety_margin_bytes) }}</template>
    </DataTable>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import StatusTag from '../components/StatusTag.vue'
import BytesText from '../components/BytesText.vue'

const rows = ref<any[] | null>(null), loading = ref(true), error = ref('')
function mb(n: any): string {
  return n === null || n === undefined ? '—' : `${(n / 1048576).toFixed(0)}MB`
}
const cols = [
  { key: 'name', title: '卷' },
  { key: 'backend', title: '后端' },
  { key: 'state', title: '状态', width: '110px', slot: 'col-state' },
  { key: 'root_ref', title: '路径' },
  { key: 'free_bytes', title: '空闲', slot: 'col-free' },
  { key: 'reserved_bytes', title: '预留', slot: 'col-reserved' },
  { key: 'safety_margin_bytes', title: '安全余量', slot: 'col-margin' },
]
async function load() {
  loading.value = true; error.value = ''
  try { rows.value = await api.storageGet('/api/v1/storage/volumes') }
  catch (x: any) { error.value = x.message || '加载失败' }
  finally { loading.value = false }
}
onMounted(load)
</script>
