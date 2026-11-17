<!-- LT-02 改造：DataTable + StatusTag。API 不变：GET /api/v1/external-blockers。 -->
<template>
  <div>
    <PageHeader title="外部阻断清单（B01—B09）"
      subtitle="每项显示责任方、影响能力、状态；缺一个字段不阻断整个存储链路，只阻断对应索引/包/目的地" />
    <DataTable :columns="cols" :rows="rows ?? []" :loading="loading" :error="error"
               empty-text="当前没有登记的外部阻断" row-key="code" @retry="load">
      <template #col-status="{ row }"><StatusTag :state="row.status" /></template>
    </DataTable>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import StatusTag from '../components/StatusTag.vue'

const rows = ref<any[] | null>(null), loading = ref(true), error = ref('')
const cols = [
  { key: 'code', title: '编号', width: '90px' },
  { key: 'title', title: '事项' },
  { key: 'owner_party', title: '责任方' },
  { key: 'impact', title: '只阻断' },
  { key: 'status', title: '状态', width: '110px', slot: 'col-status' },
]
async function load() {
  loading.value = true; error.value = ''
  try { rows.value = await api.coreGet('/api/v1/external-blockers') }
  catch (x: any) { error.value = x.message || '加载失败' }
  finally { loading.value = false }
}
onMounted(load)
</script>
