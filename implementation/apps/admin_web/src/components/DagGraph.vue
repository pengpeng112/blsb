<!-- RT-05 U07：DAG 可视化（纯 SVG，零新依赖；布局在 utils/dagLayout）。
     仅可视化：不改变 workflow 节点语义、不发起执行请求。
     stepStates 传 {node_id: StepRun.state} 时按状态着色；未知状态仍可见。 -->
<template>
  <div class="dag-graph">
    <ErrorState v-if="layout.errors.length"
                :message="`DSL 结构错误（${layout.errors.length} 项；仅展示不执行）`" />
    <ul v-if="layout.errors.length" class="dag-errors">
      <li v-for="(e, i) in layout.errors" :key="i">{{ e }}</li>
    </ul>
    <template v-else>
      <svg :width="width" :height="height" class="dag-svg" role="img"
           aria-label="工作流 DAG 图">
        <defs>
          <marker id="dag-arrow" markerWidth="8" markerHeight="8" refX="7"
                  refY="3" orient="auto">
            <path d="M0,0 L7,3 L0,6 z" fill="#94a3b8" />
          </marker>
        </defs>
        <!-- 边（on 条件着色：success灰/needs_review橙/invalid红） -->
        <g v-for="(e, i) in layout.edges" :key="'e' + i">
          <path :d="edgePath(e)" fill="none"
                :stroke="edgeColor(e.on)" stroke-width="1.5"
                marker-end="url(#dag-arrow)" />
          <text v-if="e.on !== 'success'"
                :x="(e.x1 + e.x2) / 2" :y="(e.y1 + e.y2) / 2 - 4"
                class="edge-label">{{ edgeLabel(e.on) }}</text>
        </g>
        <!-- 节点 -->
        <g v-for="n in layout.nodes" :key="n.id">
          <rect :x="n.x" :y="n.y" width="132" height="40" rx="6"
                :fill="fillOf(n.id).fill" :stroke="fillOf(n.id).stroke"
                stroke-width="1.5" />
          <text :x="n.x + 66" :y="n.y + 17" text-anchor="middle"
                class="node-label">
            <title>{{ n.full }}（{{ n.type }}）</title>{{ n.label }}</text>
          <text :x="n.x + 66" :y="n.y + 32" text-anchor="middle"
                class="node-type">{{ stateOf(n.id) || n.type }}</text>
          <circle v-if="n.isolated" :cx="n.x + 124" :cy="n.y + 8" r="4"
                   fill="none" stroke="#d97706" stroke-dasharray="2,2" />
          <title v-if="n.isolated">孤立节点：无任何入边/出边</title>
        </g>
      </svg>
      <p class="muted small">{{ layout.nodes.length }} 节点 /
        {{ layout.edges.length }} 边 / {{ layout.layers }} 层</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { layoutDag, stateFill } from '../utils/dagLayout'

const props = withDefaults(defineProps<{
  dsl: unknown
  stepStates?: Record<string, string> | null
}>(), { stepStates: null })

const layout = computed(() => layoutDag(props.dsl))
const width = computed(() => Math.max(
  320, ...layout.value.nodes.map(n => n.x + 160)))
const height = computed(() => Math.max(
  160, ...layout.value.nodes.map(n => n.y + 72)))

function fillOf (id: string) {
  const s = props.stepStates ? props.stepStates[id] : undefined
  return stateFill(s)
}
function stateOf (id: string): string {
  return props.stepStates ? (props.stepStates[id] ?? '') : ''
}
function edgePath (e: { x1: number, y1: number, x2: number, y2: number }) {
  const mx = (e.x1 + e.x2) / 2
  return `M ${e.x1} ${e.y1} C ${mx} ${e.y1}, ${mx} ${e.y2}, ${e.x2} ${e.y2}`
}
function edgeColor (on: string): string {
  return { success: '#94a3b8', needs_review: '#d97706', invalid: '#dc2626' }[on]
    ?? '#94a3b8'
}
function edgeLabel (on: string): string {
  return { needs_review: '需复核', invalid: '无效' }[on] ?? on
}
</script>

<style scoped>
.dag-svg { background: var(--bg-alt, #f8fafc); border: 1px solid var(--border, #e5e7eb);
  border-radius: 6px; }
.node-label { font-size: 12px; fill: var(--text, #0f172a); }
.node-type { font-size: 10px; fill: var(--text-muted, #64748b); }
.edge-label { font-size: 10px; fill: #b45309; }
.dag-errors { margin: 4px 0; padding-left: 18px; font-size: 12px; color: #b91c1c; }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
</style>
