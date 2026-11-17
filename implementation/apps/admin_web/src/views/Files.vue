<!-- LT-02 改造：FilterBar + DataTable + StatusTag + BytesText + Toast。
     API 与参数不变：GET /api/v1/assets[?state=]、storage POST /api/v1/assets/{id}/verify。 -->
<template>
  <div>
    <PageHeader title="文件资产" subtitle="正式文件不可变；重校验走存储服务作业" />
    <FilterBar @search="load" @reset="state = ''; load()">
      <label>状态
        <select v-model="state">
          <option value="">全部状态</option>
          <option>AVAILABLE</option><option>STAGING</option><option>QUARANTINED</option>
        </select>
      </label>
    </FilterBar>
    <DataTable :columns="cols" :rows="rows ?? []" :loading="loading" :error="error"
               empty-text="没有符合条件的文件资产" row-key="id" @retry="load">
      <template #col-role="{ row }"><span class="tag">{{ row.role }}</span></template>
      <template #col-size="{ row }"><BytesText :value="row.size_bytes" /></template>
      <template #col-sha="{ row }"><span class="mono-muted" :title="row.sha256">{{ (row.sha256 || '').slice(0, 16) }}…</span></template>
      <template #col-state="{ row }"><StatusTag :state="row.state" /></template>
      <template #col-ops="{ row }">
        <button class="ghost" @click.stop="verify(row)">重校验</button>
      </template>
    </DataTable>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import FilterBar from '../components/FilterBar.vue'
import DataTable from '../components/DataTable.vue'
import StatusTag from '../components/StatusTag.vue'
import BytesText from '../components/BytesText.vue'
import { toast } from '../components/Toast'

const state = ref(''), rows = ref<any[] | null>(null)
const loading = ref(true), error = ref('')
const cols = [
  { key: 'id', title: '资产', width: '110px', formatter: (r: any) => (r.id || '').slice(0, 8) },
  { key: 'role', title: '角色', slot: 'col-role' },
  { key: 'original_name', title: '文件名' },
  { key: 'size_bytes', title: '大小', slot: 'col-size' },
  { key: 'sha256', title: 'sha256', slot: 'col-sha' },
  { key: 'state', title: '状态', width: '120px', slot: 'col-state' },
  { key: 'ops', title: '操作', width: '100px', slot: 'col-ops' },
]
async function load() {
  loading.value = true; error.value = ''
  try {
    rows.value = await api.coreGet('/api/v1/assets' + (state.value ? '?state=' + state.value : ''))
  } catch (x: any) { error.value = x.message || '加载失败' }
  finally { loading.value = false }
}
async function verify(a: any) {
  try {
    await api.storagePost(`/api/v1/assets/${a.id}/verify`, {})
    toast.success('重校验已受理')
    await load()
  } catch (x: any) { toast.error(x.message || '重校验失败') }
}
onMounted(load)
</script>
<style scoped>
.mono-muted { font-family: var(--font-mono); color: var(--c-text-3); font-size: 12px; }
</style>
