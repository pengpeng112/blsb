<!-- LT-02 改造：PageHeader(mock) + DataTable + StatusTag + JsonViewer 抽屉 +
     ConfirmDialog(requireReason，理由传给后端 reason 字段——与旧 API 形状一致)。
     API 与参数不变：GET /api/v1/submissions、GET /api/v1/submissions/{id}、
     POST /api/v1/submissions/{id}/corrections {reason}。 -->
<template>
  <div>
    <PageHeader title="发布与上报" subtitle="待采集 / 已下载 / 平台确认 分别展示；HTTP 200 ≠ 业务确认。Mock 目标不得打开真实生产 Profile" mock />
    <DataTable :columns="cols" :rows="rows ?? []" :loading="loading" :error="error"
               empty-text="暂无提交记录" row-key="id" @retry="load"
               @row-click="r => detail(r.id)">
      <template #col-id="{ row }">{{ (row.id || '').slice(0, 8) }}</template>
      <template #col-state="{ row }">
        <StatusTag :state="row.state"
                   :map="{ CONFIRMED: '已确认', UNKNOWN: '结果未知' }" />
      </template>
      <template #col-rev="{ row }">r{{ row.package_revision?.revision }} {{ row.package_revision?.state }}</template>
      <template #col-ops="{ row }">
        <button class="ghost" @click.stop="detail(row.id)">证据</button>
        <button class="ghost" @click.stop="openCorrect(row)">更正(新版本)</button>
      </template>
    </DataTable>

    <Drawer v-model:open="detailOpen" title="提交证据详情" width="640px">
      <JsonViewer v-if="detailData" :data="detailData" :default-depth="2" />
    </Drawer>

    <ConfirmDialog v-model:open="correctOpen" v-model:reason="correctReason"
      title="更正（新版本）" confirm-text="提交更正" :require-reason="true"
      :busy="correctBusy" @confirm="submitCorrect">
      为提交 {{ correctId?.slice(0, 8) }} 生成新版本；将提交一个带理由的人工更正记录
      （理由随请求体传给后端并记审计）。
    </ConfirmDialog>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import StatusTag from '../components/StatusTag.vue'
import Drawer from '../components/Drawer.vue'
import JsonViewer from '../components/JsonViewer.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { toast } from '../components/Toast'

const rows = ref<any[] | null>(null), loading = ref(true), error = ref('')
const detailOpen = ref(false), detailData = ref<any>(null)
const correctOpen = ref(false), correctReason = ref(''), correctBusy = ref(false)
const correctId = ref<string | null>(null)
const cols = [
  { key: 'id', title: '提交', width: '100px', slot: 'col-id' },
  { key: 'destination', title: '目的地' },
  { key: 'state', title: '状态', width: '110px', slot: 'col-state' },
  { key: 'package_revision', title: '包修订', width: '130px', slot: 'col-rev' },
  { key: 'snapshot_digest', title: '快照摘要', formatter: (r: any) =>
    (r.snapshot_digest || '').slice(0, 16) + '…' },
  { key: 'ops', title: '操作', width: '180px', slot: 'col-ops' },
]
async function load() {
  loading.value = true; error.value = ''
  try { rows.value = await api.coreGet('/api/v1/submissions') }
  catch (x: any) { error.value = x.message || '加载失败' }
  finally { loading.value = false }
}
async function detail(id: string) {
  try {
    detailData.value = await api.coreGet('/api/v1/submissions/' + id)
    detailOpen.value = true
  } catch (x: any) { toast.error(x.message || '证据加载失败') }
}
function openCorrect(row: any) {
  correctId.value = row.id
  correctReason.value = ''
  correctOpen.value = true
}
async function submitCorrect(reason: string) {
  if (!correctId.value) return
  correctBusy.value = true
  try {
    await api.corePost(`/api/v1/submissions/${correctId.value}/corrections`,
      { reason })                       // 用户输入的理由原样传后端（不再写死"人工更正"）
    toast.success('更正已提交（新版本）')
    correctOpen.value = false
    await load()
  } catch (x: any) { toast.error(x.message || '更正失败') }
  finally { correctBusy.value = false }
}
onMounted(load)
</script>
