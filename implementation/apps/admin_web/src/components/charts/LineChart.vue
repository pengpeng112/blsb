<!-- SVG 折线图（多序列；y 轴自动刻度；无数据空态）。
用法：<LineChart :series="[{name:'发送',color:'#1f6feb',points:[{x:'11-01',y:12}]}]" :height="180"/>
RT-01 修复：① X 轴合并所有 series 的日期并集排序（此前只按 series[0] 的
索引布局，多序列日期集不同时相互错位）；② 全 0/空值时 yMax 至少为 1，
不出现 NaN。 -->
<template>
  <EmptyState v-if="!hasData" text="暂无时序数据" />
  <svg v-else :viewBox="`0 0 ${W} ${H}`" class="lc" role="img">
    <g v-for="(tk, i) in yTicks" :key="'g' + i">
      <line :x1="PAD_L" :x2="W - PAD_R" :y1="y(tk)" :y2="y(tk)" class="lc-grid" />
      <text :x="PAD_L - 6" :y="y(tk) + 3" class="lc-tick" text-anchor="end">{{ fmt(tk) }}</text>
    </g>
    <g v-for="s in series" :key="s.name">
      <polyline :points="linePoints(s)" fill="none" :stroke="s.color" stroke-width="2" />
      <circle v-for="p in mappedPoints(s)" :key="p.x" :cx="x(p.di)"
              :cy="y(p.y)" r="3" :fill="s.color">
        <title>{{ `${s.name} ${p.x}: ${p.y}` }}</title>
      </circle>
    </g>
    <g v-for="(d, di) in xDomain" :key="'x' + di">
      <text v-if="showEveryX(di)" :x="x(di)" :y="H - 6"
            class="lc-tick" text-anchor="middle">{{ d }}</text>
    </g>
  </svg>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import EmptyState from '../EmptyState.vue'
export interface LcPoint { x: string, y: number }
export interface LcSeries { name: string, color: string, points: LcPoint[] }
const props = withDefaults(defineProps<{ series: LcSeries[], height?: number,
  yFmt?: (n: number) => string }>(), { height: 180 })
const W = 560
const PAD_L = 44
const PAD_R = 12
const PAD_T = 10
const H = computed(() => props.height)
const hasData = computed(() =>
  props.series.some(s => s.points.some(p => Number.isFinite(p.y))))
const allY = computed(() =>
  props.series.flatMap(s => s.points.map(p => p.y)).filter(Number.isFinite))
const yMax = computed(() => Math.max(1, ...allY.value))
const yTicks = computed(() => {
  const m = yMax.value
  const step = Math.max(1, Math.ceil(m / 4))
  const ticks: number[] = []
  for (let v = 0; v <= m + step * 0.001; v += step) ticks.push(v)
  return ticks
})
/** X 轴日期并集（全 series 合并排序）——多序列不同日期集也共享同一坐标轴。 */
const xDomain = computed(() => {
  const set = new Set<string>()
  for (const s of props.series) {
    for (const p of s.points) if (Number.isFinite(p.y)) set.add(p.x)
  }
  return [...set].sort()
})
function y(v: number): number {
  const top = yTicks.value[yTicks.value.length - 1] || 1
  const inner = H.value - 24 - PAD_T
  return PAD_T + inner * (1 - v / top)
}
function x(di: number): number {
  const n = xDomain.value.length
  if (n <= 1) return PAD_L + (W - PAD_L - PAD_R) / 2
  return PAD_L + (W - PAD_L - PAD_R) * (di / (n - 1))
}
/** series 点 → {x, y, di(并集索引)}（缺失日期的点被剔除，线段自然断开）。 */
function mappedPoints(s: LcSeries): { x: string, y: number, di: number }[] {
  const idx = new Map(xDomain.value.map((d, i) => [d, i]))
  return s.points
    .filter(p => Number.isFinite(p.y) && idx.has(p.x))
    .map(p => ({ x: p.x, y: p.y, di: idx.get(p.x) as number }))
}
function linePoints(s: LcSeries): string {
  return mappedPoints(s).map(p => `${x(p.di)},${y(p.y)}`).join(' ')
}
function showEveryX(di: number): boolean {
  const n = xDomain.value.length
  if (n <= 8) return true
  return di % Math.ceil(n / 8) === 0
}
function fmt(v: number): string {
  return props.yFmt ? props.yFmt(v) : String(v)
}
</script>
<style scoped>
.lc { width: 100%; height: auto; }
.lc-grid { stroke: #e6ebf1; stroke-width: 1; }
.lc-tick { font-size: 10px; fill: var(--c-text-3); }
</style>
