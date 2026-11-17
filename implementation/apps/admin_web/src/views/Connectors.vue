<!-- RT-05 U08：连接器向导（结构化表单+高级 JSON 保留；不保存明文凭据；
     测试/coverage 按真实响应展示；本轮测试连接仅合成探测，不探测真实 HIS/PIS）。 -->
<template>
  <div>
    <PageHeader title="连接器配置向导" subtitle="结构化字段 + 高级 JSON；凭据只走环境变量/secret_ref" />
    <div class="hint-bar">
      对象名只允许来自批准允许列表；查询值全部参数绑定；凭据仅存 secret_ref/env 引用；
      连通性测试=合成探针（本轮不对真实 HIS/PIS/ODS 连接器自动探测）。
      向导：连接器 → <router-link to="/mapping">字段映射/模拟</router-link> → 字典批准 → Pack/ConfigRelease。
    </div>

    <div class="card">
      <b>新建来源连接器（草稿；服务端校验为准）</b>
      <div class="row">
        <label>插件类型 *
          <select v-model="form.plugin_id">
            <option value="pis.pitaya.sqlserver">PIS · SQL Server（Pitaya）</option>
            <option value="his.medrec.oracle">HIS · Oracle（MEDREC）</option>
          </select>
        </label>
        <label>名称 *
          <input v-model.trim="form.name" placeholder="如 pis-main" />
        </label>
        <label>来源系统编码（可选）
          <select v-model="form.source_system_code">
            <option value="">（不绑定）</option>
            <option v-for="s in sourceSystems" :key="s.code" :value="s.code">{{ s.code }}</option>
          </select>
        </label>
      </div>
      <div class="row">
        <label>数据库（结构化字段；PITAYA/QPIS 单选，不双读）
          <select v-model="form.database">
            <option value="PITAYA">PITAYA</option>
            <option value="QPIS">QPIS</option>
          </select>
        </label>
        <label>高级配置 JSON（可选；与结构化字段合并，未展示键保留）
          <input v-model.trim="advJson" placeholder='{"connect_timeout": 5}' />
        </label>
      </div>
      <p v-if="advError" class="err">{{ advError }}</p>
      <p v-if="createErr" class="err">{{ createErr }}</p>
      <div class="row">
        <button :disabled="busyCreate || !form.name" @click="create">
          {{ busyCreate ? '创建中…' : '创建（草稿）' }}</button>
        <span class="muted small">config 内如需凭据只允许 env:VAR / secret 引用；
          不在浏览器读取变量值。</span>
      </div>
    </div>

    <div class="card">
      <b>连接器</b>
      <LoadingBlock v-if="loading" />
      <ErrorState v-else-if="loadErr" :message="loadErr" @retry="load" />
      <EmptyState v-else-if="!rows.length" text="暂无连接器" />
      <table v-else class="mini-table">
        <thead><tr><th>插件</th><th>名称</th><th>方向</th><th>状态</th><th>覆盖能力</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="c in rows" :key="c.id">
            <td><code>{{ c.plugin_id }}</code></td>
            <td>{{ c.name }}</td>
            <td>{{ c.direction }}</td>
            <td><StatusTag :status="c.status" /></td>
            <td class="muted">
              <div v-for="s in c.coverage?.streams ?? []" :key="s.stream">
                {{ s.stream }}：{{ s.coverage }}</div>
            </td>
            <td class="op-cell">
              <button class="ghost" @click="test(c)">连通性测试</button>
              <button class="ghost" @click="coverage(c)">盘点覆盖</button>
            </td>
          </tr>
        </tbody>
      </table>
      <template v-if="out">
        <b>{{ outTitle }}</b>
        <!-- 真实响应结构化展示：状态/统计/JsonViewer（不支持能力与 request_id 如实） -->
        <dl class="kv" v-if="outHeadline">
          <template v-for="(v, k) in outHeadline" :key="k">
            <dt>{{ k }}</dt><dd>{{ v }}</dd>
          </template>
        </dl>
        <JsonViewer :data="out" />
      </template>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import JsonViewer from '../components/JsonViewer.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import { toast } from '../components/Toast'

const form = reactive({ plugin_id: 'pis.pitaya.sqlserver', name: '',
  source_system_code: '', database: 'PITAYA' })
const advJson = ref('')
const sourceSystems = ref<any[]>([])
const rows = ref<any[]>([])
const loading = ref(true)
const loadErr = ref('')
const busyCreate = ref(false)
const createErr = ref('')
const out = ref<any>(null)
const outTitle = ref('')

const advError = ref('')
function buildConfig(): Record<string, unknown> | null {
  const cfg: Record<string, unknown> = { database: form.database }
  const t = advJson.value.trim()
  if (t) {
    try {
      const v = JSON.parse(t)
      if (v === null || typeof v !== 'object' || Array.isArray(v)) {
        advError.value = '高级配置须为 JSON 对象'
        return null
      }
      Object.assign(cfg, v)                    // 高级键并入（不丢）
      advError.value = ''
    } catch (e: any) {
      advError.value = `高级配置 JSON 非法：${e.message}`
      return null
    }
  }
  return cfg
}

async function load(): Promise<void> {
  loading.value = true
  loadErr.value = ''
  try {
    rows.value = await api.coreGet('/api/v1/connectors')
  } catch (e: any) { loadErr.value = String(e?.message ?? e) }
  finally { loading.value = false }
}
async function create(): Promise<void> {
  const cfg = buildConfig()
  if (!cfg) return
  busyCreate.value = true
  createErr.value = ''
  try {
    await api.corePost('/api/v1/connectors', {
      plugin_id: form.plugin_id, name: form.name || 'conn-' + Date.now(),
      direction: 'source',
      source_system_code: form.source_system_code || undefined,
      config: cfg })
    toast.success('连接器已创建（草稿）')
    form.name = ''
    await load()
  } catch (e: any) {
    createErr.value = `创建失败：${e?.data?.error?.code || ''} ${e.message}（输入已保留）`
  } finally { busyCreate.value = false }
}
async function test(c: any): Promise<void> {
  outTitle.value = `连通性测试（${c.name}）`
  out.value = null
  try { out.value = await api.corePost(`/api/v1/connectors/${c.id}/test`, {}) }
  catch (e: any) {
    out.value = { error: String(e?.message ?? e),
      request_id: e?.data?.request_id ?? '' }
  }
}
async function coverage(c: any): Promise<void> {
  outTitle.value = `覆盖能力盘点（${c.name}）`
  out.value = null
  try { out.value = await api.coreGet(`/api/v1/connectors/${c.id}/coverage`) }
  catch (e: any) { out.value = { error: String(e?.message ?? e) } }
}
const outHeadline = computed(() => {
  const o = out.value
  if (!o || typeof o !== 'object') return null
  const head: Record<string, unknown> = {}
  for (const k of ['state', 'ok', 'driver_installed', 'probe', 'request_id',
    'error', 'verified_update_field']) {
    if (k in o) head[k] = o[k]
  }
  return Object.keys(head).length ? head : null
})

onMounted(async () => {
  load()
  try {
    const ss = await api.coreGet('/api/v1/source-systems')
    sourceSystems.value = Array.isArray(ss) ? ss : []
  } catch { sourceSystems.value = [] }
})
</script>

<style scoped>
.hint-bar { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af;
  padding: 8px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 12px; }
.card { background: var(--c-surface, #fff); border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px; padding: 12px; margin-bottom: 14px; }
.row { display: flex; gap: 12px; align-items: flex-end; margin: 8px 0; flex-wrap: wrap; }
.row label { display: flex; flex-direction: column; gap: 4px; font-size: 12px;
  color: var(--text-muted, #6b7280); }
.kv { display: grid; grid-template-columns: 160px 1fr; gap: 4px 10px;
  font-size: 13px; margin: 8px 0; }
.kv dt { color: var(--text-muted, #6b7280); }
.mini-table { width: 100%; font-size: 13px; border-collapse: collapse; margin: 6px 0; }
.mini-table th, .mini-table td { text-align: left; padding: 5px 6px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
.op-cell { white-space: nowrap; }
.err { color: var(--c-danger, #b91c1c); font-size: 12px; }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
</style>
