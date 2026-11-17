<!-- RT-05 U09 Metrics：本地 CSV 读取/粘贴 → 真 RFC4180 解析预览（引号换行/
     BOM/CRLF/列错逐行提示）→ 确认导入（导入前零写）→ Mock 上报（Mock 标识）。 -->
<template>
  <div>
    <PageHeader title="运营指标导入" subtitle="metrics 第二领域（无患者字段；Mock 上报标识）" mock>
      <template #actions>
        <label class="file-label">
          本地读取 CSV<input type="file" accept=".csv,.txt" @change="onFile" />
        </label>
      </template>
    </PageHeader>
    <div class="hint-bar">
      CSV 列：机构,月份,项目,数量,金额（金额为两位小数字符串）。解析为 RFC4180 语义
      （引号内逗号/换行、转义引号、BOM、CRLF），非 split(',') 假解析；导入前只预览零写库。
    </div>
    <div class="card">
      <textarea v-model="csv" rows="8" spellcheck="false"
                style="width:100%;font-family:monospace"></textarea>
      <div class="row">
        <button class="ghost" :disabled="!csv.trim()" @click="localPreview">
          本地解析预览</button>
        <button class="ghost" :disabled="!csv.trim() || busyPrev" @click="serverPreview">
          {{ busyPrev ? '服务端预览中…' : '服务端预览（脱敏/逐行错误）' }}</button>
        <button :disabled="!csv.trim() || busyImport || !importConfirmed"
                @click="importCsv">
          {{ busyImport ? '导入中…' : '确认导入并运行 Mock 上报' }}</button>
        <label class="confirm-label">
          <input type="checkbox" v-model="importConfirmed" /> 我已核对预览（导入前确认）</label>
      </div>
      <p class="muted small">导入执行的是 Mock 上报（不出网；非真实平台数据）。</p>

      <!-- 本地解析预览 -->
      <template v-if="local">
        <b>本地解析（{{ local.headers.length }} 列 / {{ local.rows.length }} 行 /
          空行跳过 {{ local.skippedEmptyLines }}）</b>
        <p v-if="local.errors.length" class="err">
          <span v-for="(e, i) in local.errors" :key="i">
            第 {{ e.line }} 行：{{ e.message }}<br /></span>
        </p>
        <table v-if="local.rows.length" class="mini-table">
          <thead><tr><th v-for="h in local.headers" :key="h">{{ h }}</th></tr></thead>
          <tbody>
            <tr v-for="(r, i) in local.rows.slice(0, 8)" :key="i">
              <td v-for="(c, j) in r" :key="j">{{ c }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="local.rows.length > 8" class="muted small">
          仅显示前 8 行（共 {{ local.rows.length }} 行）。</p>
      </template>

      <!-- 服务端预览 -->
      <template v-if="prev">
        <b>服务端预览</b>
        <dl class="kv">
          <dt>预览行数</dt><dd>{{ prev.total_preview }}</dd>
          <dt>行错误</dt>
          <dd>
            <span v-if="!prev.row_errors?.length" class="muted">无</span>
            <ul v-else class="err-list">
              <li v-for="(e, i) in prev.row_errors" :key="i">{{ e }}</li>
            </ul>
          </dd>
        </dl>
      </template>
      <template v-if="out">
        <b>导入结果（Mock 上报）</b>
        <JsonViewer :data="out" />
      </template>
    </div>
  </div>
</template>
<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import JsonViewer from '../components/JsonViewer.vue'
import { toast } from '../components/Toast'
import { parseCsvText } from '../utils/csvParse'

const csv = ref('机构,月份,项目,数量,金额\nH1234567890X,2026-08,门诊切片,120,12345.60\n')
const local = ref<ReturnType<typeof parseCsvText> | null>(null)
const prev = ref<any>(null)
const out = ref<any>(null)
const busyPrev = ref(false)
const busyImport = ref(false)
const importConfirmed = ref(false)

function onFile(ev: Event): void {
  const f = (ev.target as HTMLInputElement).files?.[0]
  if (!f) return
  const reader = new FileReader()
  reader.onload = () => {
    csv.value = String(reader.result ?? '')
    toast.info(`已读取本地文件 ${f.name}（UTF-8）`)
  }
  reader.readAsText(f, 'utf-8')
}
function localPreview(): void {
  local.value = parseCsvText(csv.value)         // 真 CSV 状态机解析
  if (local.value.errors.length) {
    toast.error(`本地解析发现 ${local.value.errors.length} 处问题（见下）`)
  }
}
async function serverPreview(): Promise<void> {
  busyPrev.value = true
  try { prev.value = await api.corePost('/api/v1/imports/preview', { csv: csv.value }) }
  catch (e: any) { toast.error(`服务端预览失败：${e.message}`) }
  finally { busyPrev.value = false }
}
async function importCsv(): Promise<void> {
  busyImport.value = true
  try {
    out.value = await api.corePost('/api/v1/imports',
      { csv: csv.value, business_key: 'monthly' }, 'ui-' + Date.now())
    toast.success('导入完成（Mock 上报已运行）')
    importConfirmed.value = false
  } catch (e: any) {
    toast.error(`导入失败：${e.message}`)
  } finally { busyImport.value = false }
}
</script>

<style scoped>
.hint-bar { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af;
  padding: 8px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 12px; }
.card { background: var(--c-surface, #fff); border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px; padding: 12px; margin-bottom: 14px; }
.row { display: flex; gap: 10px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
.confirm-label { display: flex; gap: 4px; align-items: center; font-size: 12px; }
.file-label { display: inline-flex; gap: 6px; align-items: center; font-size: 13px; }
.kv { display: grid; grid-template-columns: 110px 1fr; gap: 4px 10px;
  font-size: 13px; margin: 8px 0; }
.kv dt { color: var(--text-muted, #6b7280); }
.mini-table { width: 100%; font-size: 12px; border-collapse: collapse; margin: 6px 0; }
.mini-table th, .mini-table td { text-align: left; padding: 4px 6px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
.err { color: var(--c-danger, #b91c1c); font-size: 12px; }
.err-list { margin: 0; padding-left: 16px; }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
</style>
