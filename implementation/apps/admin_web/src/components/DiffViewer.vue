<!-- RT-05 U06：JSON 差异表（配 utils/jsonDiff 纯函数；模板只渲染）。
     敏感值由调用方在 diff 前先做安全摘要（本组件不承担脱敏）。 -->
<template>
  <div class="diff-viewer">
    <div v-if="!entries.length" class="muted small">两次内容一致（键顺序差异不产生伪差异）</div>
    <table v-else class="mini-table">
      <thead>
        <tr><th>路径</th><th>类型</th><th>{{ oldLabel }}</th><th>{{ newLabel }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="(e, i) in entries" :key="i">
          <td><code>{{ e.path }}</code></td>
          <td><span class="kind" :class="e.kind">{{ kindLabel(e.kind) }}</span></td>
          <td class="val old"><span :title="full(e.oldValue)">{{ text(e.oldValue) }}</span></td>
          <td class="val new"><span :title="full(e.newValue)">{{ text(e.newValue) }}</span></td>
        </tr>
      </tbody>
    </table>
    <p class="muted small">
      合计 {{ summary.total }} 处差异：新增 {{ summary.added }} / 删除
      {{ summary.removed }} / 修改 {{ summary.changed }} / 类型变化
      {{ summary.type_changed }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { diffJson, summarizeDiff, diffValueText } from '../utils/jsonDiff'
import type { DiffEntry } from '../utils/jsonDiff'

const props = withDefaults(defineProps<{
  oldObj?: unknown
  newObj?: unknown
  entries?: DiffEntry[]
  oldLabel?: string
  newLabel?: string
}>(), { oldLabel: '旧值', newLabel: '新值' })

const entries = computed<DiffEntry[]>(() => {
  if (props.entries) return props.entries
  return diffJson(props.oldObj, props.newObj)
})
const summary = computed(() => summarizeDiff(entries.value))
function kindLabel (k: string): string {
  return { added: '新增', removed: '删除', changed: '修改', type_changed: '类型变化' }[k] ?? k
}
function text (v: unknown): string { return diffValueText(v) }
function full (v: unknown): string {
  return typeof v === 'string' ? v : JSON.stringify(v)
}
</script>

<style scoped>
.diff-viewer { font-size: 12px; }
.kind { font-size: 11px; padding: 1px 6px; border-radius: 4px; }
.kind.added { background: #dcfce7; color: #166534; }
.kind.removed { background: #fee2e2; color: #991b1b; }
.kind.changed { background: #dbeafe; color: #1e40af; }
.kind.type_changed { background: #fef3c7; color: #92400e; }
.val.old { color: #9f1239; }
.val.new { color: #166534; }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
.mini-table { width: 100%; border-collapse: collapse; }
.mini-table th, .mini-table td { text-align: left; padding: 4px 6px;
  border-bottom: 1px solid var(--border, #e5e7eb); }
</style>
