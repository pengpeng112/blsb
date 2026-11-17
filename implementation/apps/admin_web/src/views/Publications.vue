<!-- RT-05 U09 Publications：PUBLISHED 行撤回（理由必填；服务端幂等/409 如实）；
     撤回后状态/时间更新；证据抽屉（Drawer）。真实环境发布不在本轮执行。 -->
<template>
  <div>
    <PageHeader title="视图发布" subtitle="发布快照固定资产版本；可见/下载/回执证据分列" />
    <div class="hint-bar">
      数据可见 ≠ 业务确认；下载证据与远端回执精确关联本发布。真实环境发布不在本轮执行
      （演示链路仅本机 Mock）。
    </div>
    <LoadingBlock v-if="loading" />
    <ErrorState v-else-if="loadErr" :message="loadErr" @retry="load" />
    <EmptyState v-else-if="!rows.length" text="暂无发布" />
    <table v-else class="mini-table">
      <thead><tr><th>发布</th><th>目的地</th><th>状态</th><th>代际</th><th>行数</th>
        <th>发布时间</th><th>撤回时间</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="p in rows" :key="p.id">
          <td class="muted">{{ p.id.slice(0, 8) }}</td>
          <td>{{ p.destination }}</td>
          <td><StatusTag :status="p.state" /></td>
          <td class="muted">{{ p.generation_id?.slice(0, 18) }}</td>
          <td>{{ p.items }}</td>
          <td><TimeText :value="p.published_at" /></td>
          <td><TimeText :value="p.revoked_at" /></td>
          <td class="op-cell">
            <button class="ghost" @click="evidence(p)">证据</button>
            <!-- 撤回按钮只在 PUBLISHED 行；已撤回/不可撤回与后端一致 -->
            <button v-if="p.state === 'PUBLISHED'" class="ghost"
                    @click="askRevoke(p)">撤回…</button>
            <span v-else-if="p.state === 'REVOKED'" class="muted small">已撤回</span>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 证据抽屉 -->
    <Drawer :open="!!ev" title="发布证据" width="640px" @close="ev = null">
      <template v-if="ev">
        <dl class="kv">
          <dt>数据可见</dt><dd>{{ ev.data_visible }}</dd>
          <dt>业务确认</dt><dd>{{ ev.business_confirmed }}</dd>
        </dl>
        <p class="muted small">{{ ev.note }}</p>
        <b>下载证据</b>
        <EmptyState v-if="!ev.download_evidence?.length" text="暂无下载证据" />
        <table v-else class="mini-table">
          <thead><tr><th>下载主体</th><th>字节</th><th>结果</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="(d, i) in ev.download_evidence" :key="i">
              <td>{{ d.principal }}</td><td><BytesText :value="d.bytes" /></td>
              <td>{{ d.result }}</td><td class="muted">{{ d.at }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </Drawer>

    <ConfirmDialog :open="rvOpen" title="撤回此发布" confirm-text="确认撤回"
                   :busy="rvBusy" require-reason v-model:reason="rvReason"
                   @update:open="rvOpen = $event" @confirm="doRevoke">
      撤回后视图路由逐个 revoke、清单项写墓碑；重复撤回幂等返回。
      非已撤回原因的失败会如实提示（不假成功）。
    </ConfirmDialog>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import TimeText from '../components/TimeText.vue'
import BytesText from '../components/BytesText.vue'
import Drawer from '../components/Drawer.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import { toast } from '../components/Toast'

const rows = ref<any[]>([])
const loading = ref(true)
const loadErr = ref('')
const ev = ref<any>(null)
const rvOpen = ref(false)
const rvBusy = ref(false)
const rvReason = ref('')
const rvTarget = ref<any>(null)

async function load(): Promise<void> {
  loading.value = true
  loadErr.value = ''
  try {
    const d = await api.coreGet('/api/v1/publications')
    rows.value = Array.isArray(d) ? d : (d.items ?? [])
  } catch (e: any) { loadErr.value = String(e?.message ?? e) }
  finally { loading.value = false }
}
async function evidence(p: any): Promise<void> {
  try {
    ev.value = await api.coreGet(`/api/v1/publications/${p.id}/evidence`)
    if (!rows.value.some(r => r.id === p.id && 'revoked_at' in r)) {
      // 详情可能带撤回时间——回写行（局部刷新）
      const row = rows.value.find(r => r.id === p.id)
      if (row && ev.value.revoked_at) row.revoked_at = ev.value.revoked_at
    }
  } catch (e: any) { toast.error(`证据读取失败：${e.message}`) }
}
function askRevoke(p: any): void {
  rvTarget.value = p
  rvReason.value = ''
  rvOpen.value = true
}
async function doRevoke(reason: string): Promise<void> {
  if (!rvTarget.value) return
  rvBusy.value = true
  try {
    const resp = await api.corePost(
      `/api/v1/publications/${rvTarget.value.id}/revoke`, { reason })
    if (resp?.idempotent) toast.info('该发布此前已撤回（幂等返回）')
    else toast.success('已撤回（路由 revoke + 墓碑；审计已记录）')
    rvOpen.value = false
    await load()                       // 撤回后状态/时间更新
  } catch (e: any) {
    // 409（不可撤回状态）等如实提示
    toast.error(`撤回失败：${e?.data?.error?.code || ''} ${e.message}`)
  } finally { rvBusy.value = false }
}
onMounted(load)
</script>

<style scoped>
.hint-bar { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af;
  padding: 8px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 12px; }
.mini-table { width: 100%; font-size: 13px; border-collapse: collapse; margin: 6px 0; }
.mini-table th, .mini-table td { text-align: left; padding: 5px 6px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
.op-cell { white-space: nowrap; }
.kv { display: grid; grid-template-columns: 110px 1fr; gap: 4px 10px;
  font-size: 13px; margin: 8px 0; }
.kv dt { color: var(--text-muted, #6b7280); }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
</style>
