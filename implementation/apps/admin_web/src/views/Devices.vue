<!-- LT-02 改造：双 DataTable + StatusTag + TimeText + Toast。
     API 与参数不变：GET /api/v1/agents、GET /api/v1/acquisitions、
     POST /api/v1/agents/{id}/commands {type}。 -->
<template>
  <div>
    <PageHeader title="设备采集" subtitle="命令类型白名单：apply_config / pause / resume / rescan / diagnostic / upgrade；不提供远程shell" />
    <ErrorState v-if="error" :message="error" @retry="load" />
    <LoadingBlock v-else-if="loading" />
    <template v-else>
      <div class="card">
        <b>设备</b>
        <DataTable :columns="agentCols" :rows="agents ?? []" :row-key="`id`"
                   empty-text="暂无注册设备" :row-clickable="false"
                   style="margin-top: 8px">
          <template #col-online="{ row }">
            <StatusTag :state="row.online ? 'CONFIRMED' : 'REJECTED'"
                       :map="{ CONFIRMED: '在线', REJECTED: '离线' }" />
          </template>
          <template #col-last="{ row }"><TimeText :value="row.last_seen" /></template>
          <template #col-ops="{ row }">
            <button class="ghost" @click.stop="cmd(row, 'rescan')">触发盘点</button>
            <button class="ghost" @click.stop="cmd(row, 'pause')">暂停</button>
            <button class="ghost" @click.stop="cmd(row, 'resume')">恢复</button>
          </template>
        </DataTable>
      </div>
      <div class="card">
        <b>采集事件</b>
        <DataTable :columns="acqCols" :rows="acqs ?? []" :row-key="`id`"
                   empty-text="暂无采集事件" :row-clickable="false"
                   style="margin-top: 8px">
          <template #col-state="{ row }"><StatusTag :state="row.state" /></template>
        </DataTable>
      </div>
    </template>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import StatusTag from '../components/StatusTag.vue'
import TimeText from '../components/TimeText.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import { toast } from '../components/Toast'

const agents = ref<any[] | null>(null), acqs = ref<any[] | null>(null)
const loading = ref(true), error = ref('')
const agentCols = [
  { key: 'device_code', title: '设备', formatter: (r: any) =>
    r.device_name ? `${r.device_code}（${r.device_name}）` : r.device_code },
  { key: 'source_system', title: '来源' },
  { key: 'online', title: '在线', width: '90px', slot: 'col-online' },
  { key: 'last_seen', title: '最近心跳', slot: 'col-last' },
  { key: 'ops', title: '命令', width: '240px', slot: 'col-ops' },
]
const acqCols = [
  { key: 'id', title: '事件', width: '110px', formatter: (r: any) => (r.id || '').slice(0, 8) },
  { key: 'source_scan_id', title: '扫描ID' },
  { key: 'barcode_raw', title: '条码原值' },
  { key: 'state', title: '状态', slot: 'col-state' },
]
async function load() {
  loading.value = true; error.value = ''
  try {
    agents.value = await api.coreGet('/api/v1/agents')
    acqs.value = await api.coreGet('/api/v1/acquisitions')
  } catch (x: any) { error.value = x.message || '加载失败' }
  finally { loading.value = false }
}
async function cmd(a: any, type: string) {
  try {
    await api.corePost(`/api/v1/agents/${a.id}/commands`, { type })
    toast.success(`命令 ${type} 已下发`)
    await load()
  } catch (x: any) { toast.error(x.message || '命令下发失败') }
}
onMounted(load)
</script>
