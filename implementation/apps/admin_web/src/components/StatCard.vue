<!-- 统计卡：null 值显示"—"；tone 上色；to 传入则整卡可点击下钻。
用法：<StatCard label="已确认" :value="n" unit="单" tone="success" to="/submissions"/> -->
<template>
  <component :is="to ? 'router-link' : 'div'" :to="to || undefined"
             class="stat-card" :class="[`tone-${tone}`, { clickable: !!to }]">
    <span class="stat-label">{{ label }}</span>
    <b class="stat-value">{{ value === null || value === undefined || value === '' ? '—' : value }}<small v-if="unit && value !== null">{{ unit }}</small></b>
    <span v-if="trend !== undefined" class="stat-trend" :class="trendClass">{{ trendText }}</span>
  </component>
</template>
<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ label: string, value?: number | string | null,
  unit?: string, trend?: number, tone?: 'success' | 'warning' | 'danger' | 'info',
  to?: string }>()
const trendText = computed(() =>
  props.trend === undefined ? '' : `${props.trend > 0 ? '+' : ''}${(props.trend * 100).toFixed(0)}%`)
const trendClass = computed(() =>
  props.trend === undefined ? '' : props.trend > 0 ? 'up' : props.trend < 0 ? 'down' : '')
</script>
<style scoped>
.stat-card { display: block; background: var(--c-surface); border: 1px solid var(--c-border); border-radius: var(--radius-lg); padding: 12px 14px; text-decoration: none; color: inherit; box-shadow: var(--shadow-1); }
.stat-card.clickable:hover { border-color: var(--c-primary); }
.stat-label { color: var(--c-text-2); font-size: 12px; }
.stat-value { display: block; font-size: 22px; margin-top: 4px; }
.stat-value small { font-size: 12px; color: var(--c-text-2); margin-left: 3px; font-weight: normal; }
.stat-trend { font-size: 12px; }
.tone-success .stat-value { color: var(--c-success); }
.tone-warning .stat-value { color: var(--c-warning); }
.tone-danger .stat-value { color: var(--c-danger); }
.tone-info .stat-value { color: var(--c-info); }
.stat-trend.up { color: var(--c-success); }
.stat-trend.down { color: var(--c-danger); }
</style>
