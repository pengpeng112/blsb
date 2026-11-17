<!-- SVG 柱状/堆叠柱图。
用法：<BarChart :cats="[{label:'QD-XX',values:[{name:'OPEN',color:'#c62828',value:3}]}]" :height="180" stacked/>
RT-01 修复：① 柱高比例分母用顶部刻度值 topTick（柱顶与网格线严格对齐，
此前用 yMax 造成柱顶悬在刻度之间）；② X 轴 >10 项时按步长抽稀文字（悬停
<title> 仍保留完整标签与数值），30 天趋势不再挤成黑块。 -->
<template>
  <EmptyState v-if="!hasData" text="暂无数据" />
  <svg v-else :viewBox="`0 0 ${W} ${H}`" class="bc" role="img">
    <g v-for="(tk, i) in yTicks" :key="'g' + i">
      <line :x1="PAD_L" :x2="W - PAD_R" :y1="y(tk)" :y2="y(tk)" class="bc-grid" />
      <text :x="PAD_L - 6" :y="y(tk) + 3" class="bc-tick" text-anchor="end">{{ tk }}</text>
    </g>
    <g v-for="(c, ci) in cats" :key="c.label">
      <template v-if="stacked">
        <rect v-for="(seg, si) in segsStacked(c, ci)" :key="si"
              :x="seg.x" :y="seg.y" :width="seg.w" :height="seg.h"
              :fill="seg.color" rx="2">
          <title>{{ `${c.label} ${seg.name}: ${seg.value}` }}</title>
        </rect>
      </template>
      <template v-else>
        <rect v-for="(seg, si) in segsGrouped(c, ci)" :key="si"
              :x="seg.x" :y="seg.y" :width="seg.w" :height="seg.h"
              :fill="seg.color" rx="2">
          <title>{{ `${c.label} ${seg.name}: ${seg.value}` }}</title>
        </rect>
      </template>
      <text v-if="showCatLabel(ci)" :x="catX(ci)" :y="H - 6" class="bc-tick"
            text-anchor="middle">{{ short(c.label) }}</text>
    </g>
  </svg>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import EmptyState from '../EmptyState.vue'
export interface BcSeg { name: string, color: string, value: number }
export interface BcCat { label: string, values: BcSeg[] }
const props = withDefaults(defineProps<{ cats: BcCat[], height?: number,
  stacked?: boolean }>(), { height: 180, stacked: true })
const W = 560
const PAD_L = 36
const PAD_R = 10
const PAD_T = 10
const H = computed(() => props.height)
const innerH = computed(() => H.value - 24 - PAD_T)
const hasData = computed(() =>
  props.cats.some(c => c.values.some(v => v.value > 0)))
const yMax = computed(() => {
  if (props.stacked) {
    return Math.max(1, ...props.cats.map(c =>
      c.values.reduce((a, b) => a + b.value, 0)))
  }
  return Math.max(1, ...props.cats.flatMap(c => c.values.map(v => v.value)))
})
const yTicks = computed(() => {
  const m = yMax.value
  const step = Math.max(1, Math.ceil(m / 4))
  // 顶部刻度必须 ≥ 数据峰值（RT-01：topTick 作为柱高分母，柱顶才能与
  // 顶部网格线对齐且不越界）。
  const top = Math.ceil(m / step) * step
  const t: number[] = []
  for (let v = 0; v <= top; v += step) t.push(v)
  return t
})
/** 顶部刻度值：柱高比例的分母（保证柱顶恰好落在某条网格线上）。 */
const topTick = computed(() => yTicks.value[yTicks.value.length - 1] || yMax.value)
function y(v: number): number { return PAD_T + innerH.value * (1 - v / topTick.value) }
function catX(i: number): number {
  const slot = (W - PAD_L - PAD_R) / Math.max(1, props.cats.length)
  return PAD_L + slot * i + slot / 2
}
/** X 轴标签抽稀：>10 项时隔 Math.ceil(n/8) 项显示一个（title 悬停仍完整）。 */
function showCatLabel(i: number): boolean {
  const n = props.cats.length
  if (n <= 10) return true
  return i % Math.ceil(n / 8) === 0
}
function short(label: string): string {
  return label.length > 10 ? label.slice(0, 9) + '…' : label
}
function segsStacked(c: BcCat, ci: number) {
  const slot = (W - PAD_L - PAD_R) / Math.max(1, props.cats.length)
  const bw = Math.min(36, slot * 0.6)
  const bx = catX(ci) - bw / 2
  let acc = 0
  return c.values.filter(v => v.value > 0).map(v => {
    const h0 = innerH.value * (v.value / topTick.value)
    const seg = { name: v.name, color: v.color, value: v.value,
      x: bx, y: y(acc + v.value), w: bw, h: Math.max(1, h0) }
    acc += v.value
    return seg
  })
}
function segsGrouped(c: BcCat, ci: number) {
  const slot = (W - PAD_L - PAD_R) / Math.max(1, props.cats.length)
  const n = Math.max(1, c.values.length)
  const bw = Math.min(20, (slot * 0.7) / n)
  return c.values.map((v, si) => ({
    name: v.name, color: v.color, value: v.value,
    x: catX(ci) - (bw * n) / 2 + bw * si, y: y(v.value), w: bw,
    h: Math.max(1, innerH.value * (v.value / topTick.value)),
  }))
}
</script>
<style scoped>
.bc { width: 100%; height: auto; }
.bc-grid { stroke: #e6ebf1; }
.bc-tick { font-size: 10px; fill: var(--c-text-3); }
</style>
