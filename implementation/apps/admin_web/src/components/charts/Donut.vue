<!-- SVG 环形图（中心显示总数）。
用法：<Donut :parts="[{name:'已确认',color:'#1a7f37',value:10}]" :size="160"/> -->
<template>
  <EmptyState v-if="!total" text="暂无数据" />
  <div v-else class="donut-wrap">
    <svg :viewBox="`0 0 ${size} ${size}`" :width="size" :height="size" role="img">
      <circle :cx="c" :cy="c" :r="r" fill="none" stroke="#eceff3" :stroke-width="thick" />
      <circle v-for="(seg, i) in segs" :key="i" :cx="c" :cy="c" :r="r" fill="none"
              :stroke="seg.color" :stroke-width="thick"
              :stroke-dasharray="`${seg.len} ${circ - seg.len}`"
              :stroke-dashoffset="-seg.start" stroke-linecap="butt"
              :transform="`rotate(-90 ${c} ${c})`">
        <title>{{ `${seg.name}: ${seg.value}` }}</title>
      </circle>
      <text :x="c" :y="c - 4" text-anchor="middle" class="dn-total">{{ total }}</text>
      <text :x="c" :y="c + 14" text-anchor="middle" class="dn-cap">{{ caption }}</text>
    </svg>
    <ul class="dn-legend">
      <li v-for="(seg, i) in segs" :key="i">
        <span class="dn-dot" :style="{ background: seg.color }"></span>
        {{ seg.name }} <b>{{ seg.value }}</b>
      </li>
    </ul>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import EmptyState from '../EmptyState.vue'
export interface DnPart { name: string, color: string, value: number }
const props = withDefaults(defineProps<{ parts: DnPart[], size?: number,
  caption?: string }>(), { size: 160, caption: '总计' })
const c = computed(() => props.size / 2)
const r = computed(() => props.size / 2 - 10)
const thick = computed(() => Math.max(12, props.size * 0.14))
const circ = computed(() => 2 * Math.PI * r.value)
const total = computed(() => props.parts.reduce((a, b) => a + b.value, 0))
const segs = computed(() => {
  let acc = 0
  return props.parts.filter(p => p.value > 0).map(p => {
    const len = circ.value * (p.value / total.value)
    const seg = { ...p, len, start: acc }
    acc += len
    return seg
  })
})
</script>
<style scoped>
.donut-wrap { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
.dn-total { font-size: 22px; fill: var(--c-text); font-weight: 600; }
.dn-cap { font-size: 11px; fill: var(--c-text-3); }
.dn-legend { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.dn-dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; }
</style>
