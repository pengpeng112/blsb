<template>
  <div>
    <h2>流程编排（受限DAG编辑与执行闭环）</h2>
    <p v-if="loadErr" class="tag err">{{ loadErr }}</p>

    <!-- ================= DSL 编辑器 ================= -->
    <div class="card">
      <b>① 编辑 DSL（选节点 / 条件分支）</b>
      <div class="row wrap">
        <label>workflow_key <input v-model="dsl.workflow_key" size="14" /></label>
        <button @click="addNode">＋ 添加节点</button>
        <button @click="validate">② 校验</button>
        <span :class="verdict?.valid ? 'tag ok' : 'tag err'">{{ verdict ? (verdict.valid ? '校验通过' : '校验失败') : '' }}</span>
        <button @click="save(false)">③ 保存草稿</button>
        <button @click="save(true)" :disabled="!verdict?.valid">④ 批准发布</button>
      </div>
      <table class="mini">
        <thead><tr><th>节点id</th><th>类型</th><th>插件key</th><th></th></tr></thead>
        <tbody>
          <tr v-for="n in dsl.nodes" :key="n.id">
            <td><input v-model="n.id" size="10" /></td>
            <td>
              <select v-model="n.type">
                <option v-for="t in NODE_TYPES" :key="t" :value="t">{{ t }}</option>
              </select>
            </td>
            <td><input v-model="n.plugin_ref.key" size="24" /></td>
            <td><button @click="removeNode(n)">✕</button></td>
          </tr>
        </tbody>
      </table>
      <div class="row wrap" style="margin-top:8px">
        <b>边（条件分支）</b>
        <button @click="addEdge">＋ 添加边</button>
      </div>
      <table class="mini">
        <thead><tr><th>from</th><th>to</th><th>条件 on</th><th></th></tr></thead>
        <tbody>
          <tr v-for="(e, i) in dsl.edges" :key="i">
            <td>
              <select v-model="e.from"><option v-for="n in dsl.nodes" :key="n.id" :value="n.id">{{ n.id }}</option></select>
            </td>
            <td>
              <select v-model="e.to"><option v-for="n in dsl.nodes" :key="n.id" :value="n.id">{{ n.id }}</option></select>
            </td>
            <td>
              <select v-model="e.on">
                <option value="success">success</option>
                <option value="needs_review">needs_review</option>
                <option value="invalid">invalid</option>
              </select>
            </td>
            <td><button @click="dsl.edges.splice(i, 1)">✕</button></td>
          </tr>
        </tbody>
      </table>
      <details style="margin-top:6px"><summary class="muted">DSL JSON（高级；非空非法将拒绝载入并保留原文）</summary>
        <textarea :value="dslJson" rows="6" style="width:100%;font-family:monospace"
          @input="loadJson($event)"></textarea>
      </details>
      <p v-if="msg" :class="msg.err ? 'tag err' : 'tag ok'">{{ msg.text }}</p>
      <ul v-if="verdict && !verdict.valid"><li v-for="e in verdict.errors" :key="e" style="color:#9c2b2b">{{ e }}</li></ul>
      <!-- RT-05 U07：DAG 可视化（纯 SVG；布局/环/悬空边在 utils/dagLayout） -->
      <details style="margin-top:6px" open><summary class="muted">DAG 预览（结构错误在此可见）</summary>
        <DagGraph :dsl="dsl" />
      </details>
    </div>

    <!-- ================= 已发布流程 ================= -->
    <div class="card">
      <b>已发布流程</b>
      <div v-for="w in rows" :key="w.id" class="wfitem">
        <b>{{ w.workflow_key }} r{{ w.revision }}</b>
        <span class="tag" :class="w.status === 'PUBLISHED' ? 'ok' : ''">{{ w.status }}</span>
        <span v-if="w.production_enabled" class="tag err">production_enabled（受B01—B09约束）</span>
        <DagGraph :dsl="w.dsl" />
        <div class="row wrap">
          <label>执行context（如 package_id）
            <input v-model="execCtx[w.workflow_key]" size="30" placeholder='{"package_id":"...","rows":[]}' />
          </label>
          <button @click="execute(w.workflow_key, false)">▶ 执行</button>
          <button @click="execute(w.workflow_key, true)">🧪 脱敏模拟（dry-run）</button>
        </div>
      </div>
    </div>

    <!-- ================= Runs ================= -->
    <div class="card">
      <b>运行（失败定位 / 取消 / 固定版本回放）</b>
      <div class="row wrap">
        <button @click="refreshRuns">↻ 刷新</button>
        <button v-if="runsNext" @click="nextPage">下一页（游标）</button>
      </div>
      <table class="mini">
        <thead><tr><th>run</th><th>workflow</th><th>状态</th><th>触发</th><th>时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="r in runs" :key="r.id">
            <td><code>{{ r.id.slice(0, 8) }}</code></td>
            <td>{{ r.workflow_key }}</td>
            <td><span :class="'tag ' + tagOf(r.state)">{{ r.state }}</span>
              <span v-if="r.dry_run" class="tag">dry</span></td>
            <td>{{ r.trigger }}</td>
            <td>{{ r.created_at?.replace('T', ' ').slice(0, 19) }}</td>
            <td>
              <button @click="detail(r.id)">详情</button>
              <button v-if="!isTerminal(r.state)" @click="cancel(r.id)">取消</button>
              <button v-if="isTerminal(r.state)" @click="replay(r.id)">回放</button>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="runDetail" class="detail">
        <b>run {{ runDetail.id.slice(0, 8) }} · {{ runDetail.workflow_key }}
          r{{ runDetail.workflow_revision }} · {{ runDetail.state }}</b>
        <p class="muted">依赖闭包：pack={{ (runDetail.config_closure?.pack_revision_id || '—').slice(0, 8) }}
          release={{ (runDetail.config_closure?.config_release_id || '—').slice(0, 8) }}</p>
        <table class="mini">
          <thead><tr><th>步骤</th><th>状态</th><th>错误（失败定位）</th></tr></thead>
          <tbody>
            <tr v-for="s in runDetail.steps" :key="s.node_id">
              <td>{{ s.node_id }}</td>
              <td><span :class="'tag ' + tagOf(s.state)">{{ s.state }}</span></td>
              <td style="color:#9c2b2b">{{ s.error?.message || s.error?.code || '' }}</td>
            </tr>
          </tbody>
        </table>
        <!-- 按 node_id 与真实 Step 状态着色；等待/未知不伪装成功 -->
        <details><summary class="muted">步骤图（按真实 Step 状态着色）</summary>
          <DagGraph :dsl="runDetail.dsl || {}" :step-states="stepStates" />
        </details>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import DagGraph from '../components/DagGraph.vue'
const NODE_TYPES = ['resolve', 'validate', 'wait_dependencies', 'package.freeze', 'parse',
  'target.map', 'target.validate', 'deliver.push', 'receipt.wait', 'deliver.publish']
const rows = ref<any[] | null>(null)
const verdict = ref<any>(null)
const runs = ref<any[]>([])
const runsNext = ref<string | null>(null)
const runDetail = ref<any>(null)
const execCtx = ref<Record<string, string>>({})
const msg = ref<any>(null)
const dsl = ref<any>({ workflow_key: 'demo.branch', nodes: [], edges: [] })

const dslJson = computed(() => JSON.stringify(dsl.value, null, 1))
function loadJson(ev: any) {
  try {
    const v = JSON.parse(ev.target.value)
    if (v === null || typeof v !== 'object' || Array.isArray(v)) {
      show('DSL JSON 须为对象（原文保留）', true)
      return
    }
    dsl.value = v; show('JSON已载入')
  } catch (e: any) { show(`JSON解析失败：${e.message}（原文保留）`, true) }
}
function addNode() {
  dsl.value.nodes.push({ id: 'n' + (dsl.value.nodes.length + 1), type: 'validate', plugin_ref: { key: 'pathology.quality', version: '1.0' }, parameters: {}, timeout_seconds: 300, retry: { max_attempts: 3, initial_delay_seconds: 1, max_delay_seconds: 30 } })
}
function removeNode(n: any) {
  dsl.value.nodes = dsl.value.nodes.filter((x: any) => x !== n)
  dsl.value.edges = dsl.value.edges.filter((e: any) => e.from !== n.id && e.to !== n.id)
}
function addEdge() { dsl.value.edges.push({ from: dsl.value.nodes[0]?.id, to: dsl.value.nodes[1]?.id, on: 'success' }) }
function show(text: string, err = false) { msg.value = { text, err } }
function tagOf(state: string) {
  return ({ SUCCEEDED: 'ok', FAILED: 'err', CANCELLED: 'err', NEEDS_REVIEW: 'warn' } as any)[state] || ''
}
function isTerminal(s: string) { return ['SUCCEEDED', 'FAILED', 'CANCELLED', 'NEEDS_REVIEW'].includes(s) }

const stepStates = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const st of runDetail.value?.steps || []) out[st.node_id] = st.state
  return out
})
const loadErr = ref('')
async function load() {
  try { rows.value = await api.coreGet('/api/v1/workflows'); loadErr.value = '' }
  catch (e: any) { loadErr.value = `已发布流程加载失败：${e.message}`; return }
  loadRuns()
}
async function validate() {
  try { verdict.value = await api.corePost('/api/v1/workflows/validate', { dsl: dsl.value }) }
  catch (e: any) { verdict.value = { valid: false, errors: [e.message] } }
}
async function save(publish: boolean) {
  try {
    const r = await api.corePost('/api/v1/workflows', { dsl: dsl.value, publish })
    show(`${publish ? '已发布' : '已保存'} revision=${r.revision} (${r.status})`)
    rows.value = await api.coreGet('/api/v1/workflows')
  } catch (e: any) { show(`保存失败：${e.data?.error?.code || ''} ${e.message}`, true) }
}
/** RT-05 U07：执行/保存/发布/dry-run 共用 context 校验——
 * 空白输入按明示规则视作空对象；**非空非法必须报错并零 POST**
 * （修复原空 catch 吞非法 JSON 静默当 {} 执行）。 */
function parseExecCtx(key: string): { ok: boolean, value?: any, error?: string } {
  const raw = (execCtx.value[key] ?? '').trim()
  if (!raw) return { ok: true, value: {} }        // 空白=空对象（明示）
  try {
    const v = JSON.parse(raw)
    if (v === null || typeof v !== 'object' || Array.isArray(v)) {
      return { ok: false, error: '执行 context 须为 JSON 对象' }
    }
    return { ok: true, value: v }
  } catch (e: any) {
    return { ok: false, error: `执行 context JSON 非法：${e.message}（原文已保留，修正后再执行）` }
  }
}
async function execute(key: string, dry: boolean) {
  const parsed = parseExecCtx(key)
  if (!parsed.ok) {
    show(`${dry ? "dry-run" : "执行"}被阻止：${parsed.error}`, true)
    return                                        // 零 POST
  }
  try {
    const r = await api.corePost('/api/v1/workflow-runs', { workflow_key: key, context: parsed.value, dry_run: dry }, 'run-' + Date.now())
    show(`${dry ? 'dry-run' : '执行'}已创建 run=${r.run_id.slice(0, 8)}`)
    loadRuns()
  } catch (e: any) { show(`执行失败：${e.data?.error?.code || ''} ${e.message}`, true) }
}
async function loadRuns(cursor?: string) {
  const d = await api.coreGet('/api/v1/workflow-runs?limit=20' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''))
  runs.value = cursor ? runs.value.concat(d.items) : d.items
  runsNext.value = d.next_cursor
}
function nextPage() { if (runsNext.value) loadRuns(runsNext.value) }
function refreshRuns() { loadRuns() }
async function detail(id: string) { runDetail.value = await api.coreGet('/api/v1/workflow-runs/' + id) }
async function cancel(id: string) {
  try { await api.corePost(`/api/v1/workflow-runs/${id}/cancel`, {}); loadRuns(); detail(id) }
  catch (e: any) { show(`取消失败：${e.data?.error?.code || ''} ${e.message}`, true) }
}
async function replay(id: string) {
  try {
    const r = await api.corePost(`/api/v1/workflow-runs/${id}/replay`, {})
    show(`已按固定版本回放 → 新run=${r.run_id.slice(0, 8)}（wf r${r.workflow_revision}）`)
    loadRuns()
  } catch (e: any) { show(`回放失败：${e.data?.error?.code || ''} ${e.message}`, true) }
}
onMounted(load)
</script>
<style scoped>
.canvas { background: #f2f5fa; border-radius: 8px; padding: 12px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin: 10px 0; }
.node { background: #fff; border: 1px solid #c8d6ea; border-radius: 8px; padding: 6px 10px; }
.node em { font-style: normal; font-size: 11px; }
table.mini { border-collapse: collapse; margin-top: 6px; }
table.mini th, table.mini td { border: 1px solid #d8e0ea; padding: 3px 8px; font-size: 12px; }
.wfitem { border-top: 1px solid #e3e9f0; padding: 8px 0; }
.detail { border-top: 1px solid #e3e9f0; margin-top: 8px; padding-top: 8px; }
.tag.warn { background: #f4e2c5; }
.row.wrap { flex-wrap: wrap; gap: 8px; align-items: center; }
</style>
