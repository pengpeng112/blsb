<!-- LT-06B 外部台账导入面板（传染病等 EXTERNAL 业务）：本机副本文件 →
     base64 上传；先 dry-run 出统计，确认后正式导入。 -->
<template>
  <div>
    <div class="imp-form card">
      <b>上传副本</b>
      <p class="muted">选择 state 目录副本文件（receipts-*.jsonl / sent.jsonl /
        watermark.json）；单次 ≤20MB；文件只进平台只读台账，不触生产。</p>
      <div class="row">
        <input ref="fileEl" type="file" multiple
               accept=".jsonl,.json" @change="onFiles" />
      </div>
      <div class="row">
        <input v-model="sourceLabel" placeholder="来源说明（如 <prod-host> state 副本 2026-11-05）"
               style="flex:1" />
        <input v-model="sourceInstance" placeholder="来源实例标识（幂等身份；默认 default）" />
      </div>
      <div class="row">
        <select v-model="sourceTz">
          <option value="AUTO_MIXED">标准生产混合口径（推荐：回执 UTC + 源数据北京时间 CST）</option>
          <option value="CST">全部按北京时间 CST 解释（仅旧版纯手工副本）</option>
          <option value="UTC">全部按容器 UTC 解释（仅 receipts 单文件副本）</option>
        </select>
        <button class="ghost" :disabled="!files.length || dryRunning"
                @click="doImport(true)">Dry-run 预演（不写库）</button>
        <button :disabled="!files.length || !dryRunOk || importing"
                @click="doImport(false)">正式导入</button>
      </div>
      <p class="muted">副本内时间戳无时区偏移：生产标准混合口径=回执 ts 按容器 UTC、
        已发账/水位按源端北京时间分别解释（两者本就相差 8 小时——旧单一口径必差 8 小时已根除）。</p>
    </div>

    <div v-if="dryRunReport" class="card report-card">
      <b>Dry-run 预演结果</b>
      <ul class="kv">
        <li>文件：{{ dryRunReport.files?.length ?? 0 }} 个</li>
        <li>读取行：{{ dryRunReport.rows_read }}</li>
        <li>解析记录：{{ dryRunReport.records }}</li>
        <li>拒绝行：{{ dryRunReport.rows_rejected }}</li>
        <li>已发账关联：{{ dryRunReport.sent_entries }}</li>
      </ul>
      <p v-if="dryRunReport.rejects?.length" class="warn-line">
        拒绝原因（前 {{ dryRunReport.rejects.length }} 条）：
        {{ dryRunReport.rejects.map((r: any) => r.reason).join('、') }}
      </p>
      <p class="muted">确认统计符合预期后点"正式导入"（同批文件与参数）。</p>
    </div>
    <div v-if="importReport" class="card report-card ok-card">
      <b>导入完成（批次 {{ importReport.batch_id?.slice(0, 8) }}）</b>
      <ul class="kv">
        <li>新应用：{{ importReport.rows_applied }}</li>
        <li>重复：{{ importReport.rows_skipped_duplicate }}</li>
        <li>冲突待核：{{ importReport.conflicts ?? 0 }}</li>
        <li>拒绝：{{ importReport.rows_rejected }}</li>
      </ul>
    </div>

    <h4>导入批次</h4>
    <DataTable :columns="cols" :rows="batches" :loading="loading"
               :error="error" @retry="load" empty-text="暂无导入批次" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { importExternal, listImports } from '../../api/index'
import type { ExternalImportBatchItem } from '../../api/types'
import DataTable from '../../components/DataTable.vue'
import { toast } from '../../components/Toast'
import { formatHospitalTime } from '../../utils/time'

const props = defineProps<{ target: string }>()

const fileEl = ref<HTMLInputElement | null>(null)
const files = ref<File[]>([])
const sourceLabel = ref('')
const sourceInstance = ref('')
const sourceTz = ref<'AUTO_MIXED' | 'CST' | 'UTC'>('AUTO_MIXED')
const dryRunning = ref(false)
const importing = ref(false)
const dryRunOk = ref(false)
const dryRunReport = ref<any>(null)
const importReport = ref<any>(null)
const batches = ref<ExternalImportBatchItem[]>([])
const loading = ref(false)
const error = ref('')

const cols = [
  { key: 'started_at', title: '时间', formatter: (r: ExternalImportBatchItem) =>
    formatHospitalTime(r.started_at) },
  { key: 'source_label', title: '来源说明' },
  { key: 'source_instance', title: '实例', width: '120px' },
  { key: 'source_tz', title: '时区', width: '60px' },
  { key: 'rows_read', title: '读取', align: 'right' as const },
  { key: 'rows_applied', title: '应用', align: 'right' as const },
  { key: 'rows_skipped_duplicate', title: '重复', align: 'right' as const },
  { key: 'rows_rejected', title: '拒绝', align: 'right' as const },
  { key: 'dry_run', title: '模式', formatter: (r: ExternalImportBatchItem) =>
    r.dry_run ? 'dry-run' : '正式' },
]

function onFiles(e: Event): void {
  const input = e.target as HTMLInputElement
  files.value = Array.from(input.files ?? [])
  dryRunOk.value = false
  dryRunReport.value = null
}

async function toPayload(): Promise<{ name: string, content_base64: string }[]> {
  const out: { name: string, content_base64: string }[] = []
  for (const f of files.value) {
    const buf = await f.arrayBuffer()
    let bin = ''
    const bytes = new Uint8Array(buf)
    const chunk = 0x8000
    for (let i = 0; i < bytes.length; i += chunk) {
      bin += String.fromCharCode(...bytes.subarray(i, i + chunk))
    }
    out.push({ name: f.name, content_base64: btoa(bin) })
  }
  return out
}

async function doImport(dry: boolean): Promise<void> {
  const payload = await toPayload()
  if (dry) {
    dryRunning.value = true
  } else {
    importing.value = true
  }
  try {
    const r = await importExternal({
      target: props.target,
      source_label: sourceLabel.value || '控制台上传',
      source_instance: sourceInstance.value || 'console',
      source_tz: sourceTz.value, files: payload, dry_run: dry })
    if (dry) {
      dryRunReport.value = r
      dryRunOk.value = true
      toast.success('Dry-run 预演完成（未写库）')
    } else {
      importReport.value = r
      dryRunReport.value = null
      dryRunOk.value = false
      if (fileEl.value) fileEl.value.value = ''
      files.value = []
      toast.success('正式导入完成')
      load()
    }
  } catch (e: any) {
    toast.error(`导入失败：${e?.message ?? e}`)
  } finally {
    dryRunning.value = false
    importing.value = false
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    batches.value = await listImports(props.target)
  } catch (e: any) {
    error.value = String(e?.message ?? e)
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.imp-form { display: flex; flex-direction: column; gap: 8px; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.report-card { margin-top: 10px; }
.ok-card { border-color: #86efac; }
.kv { font-size: 13px; list-style: none; padding-left: 0; }
.warn-line { background: #fffbeb; border: 1px solid #fde68a; color: #92400e;
  padding: 6px 10px; border-radius: 6px; font-size: 12px; }
</style>
