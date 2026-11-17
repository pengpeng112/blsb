<!-- RT-05 U06：医院配置包 + 批准字典 + ConfigRelease（撤销/恢复真实语义）。
     - 修复原重复表达式 p.content.pack_key || p.content.pack_key（真实备用
       字段为 content.note 的 pack 语义→pack_key 缺失时回退 id 前缀展示，
       不虚构属性）；
     - 业务结构 JsonViewer（不再裸 pre JSON）；
     - 发布列表：撤销此发布（reason 必填审计）/ 恢复到此版本（先 Diff
       源闭包 vs 最新未回退闭包 → reason 确认 → POST restore 创建新发布）；
       rollback=撤销标记、restore=以历史不可变闭包创建新发布——语义分立；
     - 恢复不自动启用调度/发送；失败/409 如实展示。 -->
<template>
  <div>
    <PageHeader title="医院配置包" subtitle="Pack / 批准字典 / ConfigRelease 发布与恢复" />
    <div class="hint-bar">
      向导：连接器 → 字段映射（字段映射/模拟）→ 本页字典批准 → Pack 发布 →
      ConfigRelease。发布闭包=workflow/pack/字典/mapping/schema/destination/
      connector 的具体 revision+digest；字典未批准将拒绝（409）。
    </div>

    <!-- Packs -->
    <LoadingBlock v-if="packLoading" />
    <ErrorState v-else-if="packError" :message="packError" @retry="loadAll" />
    <template v-else>
      <div class="card">
        <b>医院配置包修订</b>
        <EmptyState v-if="!rows.length" text="暂无 Pack 修订" />
        <div v-for="p in rows" :key="p.id" class="pack-item">
          <div class="pack-head">
            <b><code>{{ p.content.pack_key || p.id.slice(0, 8) }}</code>
              r{{ p.revision }}</b>
            <StatusTag :status="p.state" />
            <span v-if="p.content.production_enabled" class="prod-flag">
              生产启用（受阻断约束）</span>
          </div>
          <JsonViewer :data="p.content" :default-depth="1" />
        </div>
      </div>

      <!-- 字典 -->
      <div class="card">
        <b>批准字典</b>
        <EmptyState v-if="!dicts.length" text="暂无字典修订" />
        <table v-else class="mini-table">
          <thead><tr><th>命名空间</th><th>版本</th><th>条目</th><th>批准人</th></tr></thead>
          <tbody>
            <tr v-for="d in dicts" :key="d.id">
              <td><code>{{ d.namespace }}</code></td>
              <td>{{ d.version }}</td>
              <td class="muted">
                {{ (d.items || []).map((i: any) => `${i.code}→${i.target}`).join('；') }}</td>
              <td class="muted">{{ d.approved_by || '未批准' }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted small">review-draft 包不引用字典时全部映射不生效
          （QF-ID-TYPE/QF-ORG/QF-CATEGORY 等待）。</p>
      </div>
    </template>

    <!-- ConfigRelease -->
    <div class="card">
      <b>ConfigRelease（撤销=标记；恢复=以历史不可变闭包创建新发布）</b>
      <div class="row">
        <input v-model="releaseNote" placeholder="发布备注（可选）" style="width:320px" />
        <button :disabled="pubBusy" @click="publishRelease">
          {{ pubBusy ? '发布中…' : '发布 ConfigRelease' }}</button>
      </div>
      <p v-if="releaseErr" class="err">{{ releaseErr }}</p>
      <LoadingBlock v-if="relLoading" />
      <EmptyState v-else-if="!releases.length" text="暂无发布（发布一次即产生可审计闭包）" />
      <table v-else class="mini-table">
        <thead><tr><th>release</th><th>digest（前16）</th><th>条目</th>
          <th>来源</th><th>发布人</th><th>时间</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="r in releases" :key="r.id">
            <td><code>{{ r.id.slice(0, 8) }}</code></td>
            <td><code>{{ (r.digest || '').slice(0, 16) }}</code></td>
            <td>{{ (r.revision_ids || []).length || '—' }}</td>
            <td class="muted">{{ restoredFrom(r) ? `恢复自 ${restoredFrom(r).slice(0, 8)}` : '—' }}</td>
            <td class="muted">{{ r.released_by || '—' }}</td>
            <td><TimeText :value="r.released_at" /></td>
            <td>
              <StatusTag :status="r.rolled_back ? '已回退' : (isLatestActive(r) ? '生效中' : '历史')"
                         :map="{ 已回退: '已回退', 生效中: 'OK', 历史: '历史' }" />
            </td>
            <td class="op-cell">
              <button v-if="!r.rolled_back" class="ghost mini"
                      @click="askRollback(r)">撤销此发布…</button>
              <button class="ghost mini" :disabled="r.id === latestActiveId"
                      @click="askRestore(r)">恢复到此版本…</button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 恢复前 Diff（源闭包 vs 最新未回退闭包；敏感配置值显示安全摘要） -->
      <Drawer :open="diffOpen" title="恢复前差异（源闭包 vs 当前生效闭包）" width="720px"
              @close="diffOpen = false">
        <template v-if="restoreTarget">
          <p class="muted small">来源发布
            <code>{{ restoreTarget.id.slice(0, 8) }}</code>
            （digest {{ restoreTarget.digest.slice(0, 16) }}，条目
            {{ (restoreTarget.revision_ids || []).length }}）。恢复会**创建新发布**
            指向该历史闭包；不改写历史冻结包、不退休较新修订、不自动启用调度/发送。</p>
          <DiffViewer :old-obj="latestActiveClosure" :new-obj="restoreTarget.revision_ids"
                      old-label="当前生效闭包" new-label="恢复目标闭包" />
          <label>理由 *（≥5 字，进审计）</label>
          <textarea v-model="restoreReason" rows="2"></textarea>
          <p v-if="restoreErr" class="err">
            {{ restoreErr }}
            <span v-if="restoreRequestId" class="muted">request_id={{ restoreRequestId }}</span>
          </p>
          <div class="drawer-actions">
            <button class="ghost" @click="diffOpen = false">取消</button>
            <button :disabled="restoreBusy || restoreReason.trim().length < 5"
                    @click="doRestore">
              {{ restoreBusy ? '恢复中…' : '确认恢复（创建新发布）' }}</button>
          </div>
        </template>
      </Drawer>
    </div>

    <ConfirmDialog :open="rbOpen" title="撤销此发布（标记，非恢复）"
                   confirm-text="确认撤销" :busy="rbBusy"
                   require-reason v-model:reason="rbReason"
                   @update:open="rbOpen = $event" @confirm="doRollback">
      撤销仅标记本发布 rolled_back_at（run 闭包回落到最近未回退发布）；
      **不会恢复任何配置**。要回到历史版本请用"恢复到此版本"。
    </ConfirmDialog>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { listConfigReleases, rollbackRelease, restoreRelease } from '../api/ops'
import type { ConfigReleaseItem } from '../api/ops'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import TimeText from '../components/TimeText.vue'
import JsonViewer from '../components/JsonViewer.vue'
import DiffViewer from '../components/DiffViewer.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import Drawer from '../components/Drawer.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { toast } from '../components/Toast'

const rows = ref<any[]>([])
const dicts = ref<any[]>([])
const packLoading = ref(true)
const packError = ref('')
const releases = ref<ConfigReleaseItem[]>([])
const relLoading = ref(true)
const releaseNote = ref('')
const releaseErr = ref('')
const pubBusy = ref(false)

const latestActiveId = computed(() => {
  const act = releases.value.filter(r => !r.rolled_back)
  return act.length ? act[act.length - 1].id : ''
})
function isLatestActive(r: ConfigReleaseItem): boolean {
  return !r.rolled_back && r.id === latestActiveId.value
}
function restoredFrom(r: ConfigReleaseItem): string {
  return String((r.test_report as any)?.restored_from ?? '')
}

async function loadAll(): Promise<void> {
  packLoading.value = true
  packError.value = ''
  try {
    const [packs, ds] = await Promise.all([
      api.coreGet('/api/v1/hospital-pack'), api.coreGet('/api/v1/dictionaries')])
    rows.value = Array.isArray(packs) ? packs : []
    dicts.value = Array.isArray(ds) ? ds : []
  } catch (e: any) {
    packError.value = String(e?.message ?? e)
  } finally { packLoading.value = false }
  await loadReleases()
}
async function loadReleases(): Promise<void> {
  relLoading.value = true
  try { releases.value = await listConfigReleases() }
  catch (e: any) { releaseErr.value = `读取发布列表失败：${e.message}` }
  finally { relLoading.value = false }
}

async function publishRelease(): Promise<void> {
  releaseErr.value = ''
  pubBusy.value = true
  try {
    await api.corePost('/api/v1/config-releases', { note: releaseNote.value })
    releaseNote.value = ''
    toast.success('ConfigRelease 已发布（闭包快照）')
    await loadReleases()                        // 全量刷新（含 digest/条目）
  } catch (e: any) {
    releaseErr.value = `发布失败：${e?.data?.error?.code || ''} ${e.message}`
  } finally { pubBusy.value = false }
}

// ---------------- 撤销（rollback：标记语义） ----------------
const rbOpen = ref(false)
const rbBusy = ref(false)
const rbReason = ref('')
const rbTarget = ref<ConfigReleaseItem | null>(null)
function askRollback(r: ConfigReleaseItem): void {
  rbTarget.value = r
  rbReason.value = ''
  rbOpen.value = true
}
async function doRollback(reason: string): Promise<void> {
  if (!rbTarget.value) return
  rbBusy.value = true
  try {
    const resp = await rollbackRelease(rbTarget.value.id, reason)
    toast.success(resp?.already_rolled_back
      ? '该发布此前已撤销（幂等返回）' : '发布已撤销（后续 run 回落最近未回退发布）')
    rbOpen.value = false
    await loadReleases()
  } catch (e: any) {
    toast.error(`撤销被拒绝：${e?.message ?? e}`)   // 失败不假成功
  } finally { rbBusy.value = false }
}

// ---------------- 恢复（restore：历史闭包 → 新发布） ----------------
const diffOpen = ref(false)
const restoreTarget = ref<ConfigReleaseItem | null>(null)
const restoreReason = ref('')
const restoreBusy = ref(false)
const restoreErr = ref('')
const restoreRequestId = ref('')
const latestActiveClosure = computed(() => {
  const act = releases.value.filter(r => !r.rolled_back)
  return act.length ? act[act.length - 1].revision_ids ?? [] : []
})
function askRestore(r: ConfigReleaseItem): void {
  restoreTarget.value = r
  restoreReason.value = ''
  restoreErr.value = ''
  restoreRequestId.value = ''
  diffOpen.value = true
}
async function doRestore(): Promise<void> {
  if (!restoreTarget.value) return
  restoreBusy.value = true
  restoreErr.value = ''
  try {
    const resp = await restoreRelease(restoreTarget.value.id, restoreReason.value)
    toast.success(resp.idempotent
      ? `该历史版本已是当前生效闭包（幂等；release ${resp.release_id.slice(0, 8)}）`
      : `已创建恢复发布 ${resp.release_id.slice(0, 8)}（${resp.entries} 条目）`)
    diffOpen.value = false
    await loadReleases()
  } catch (e: any) {
    restoreErr.value = String(e?.message ?? e)
    restoreRequestId.value = String(e?.request_id ?? e?.data?.request_id ?? '')
  } finally { restoreBusy.value = false }
}

onMounted(loadAll)
</script>

<style scoped>
.hint-bar { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af;
  padding: 8px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 12px; }
.card { background: var(--c-surface, #fff); border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px; padding: 12px; margin-bottom: 14px; }
.pack-item { border-top: 1px dashed var(--border, #e5e7eb); padding: 8px 0; }
.pack-head { display: flex; gap: 8px; align-items: center; margin-bottom: 4px; }
.prod-flag { background: var(--c-danger-weak, #fee2e2); color: var(--c-danger, #b91c1c);
  font-size: 11px; padding: 1px 6px; border-radius: 4px; }
.row { display: flex; gap: 8px; align-items: center; margin: 8px 0; }
.mini-table { width: 100%; font-size: 13px; border-collapse: collapse; margin: 6px 0; }
.mini-table th, .mini-table td { text-align: left; padding: 5px 6px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
.op-cell { white-space: nowrap; }
button.mini { padding: 0 6px; font-size: 11px; }
.err { color: var(--c-danger, #b91c1c); font-size: 12px; }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
.drawer-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
label { display: block; font-size: 12px; color: var(--text-muted, #6b7280); margin: 10px 0 2px; }
textarea { width: 100%; box-sizing: border-box; }
</style>
