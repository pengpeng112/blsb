<!-- 状态标签：bucket（统一桶）/ state / status（RT-01 三种传参等价）+ map（自定义映射）。
用法：<StatusTag bucket="CONFIRMED"/> 或 <StatusTag state="WEIRD" :map="{WEIRD:'怪'}"/>
或 <StatusTag :status="run.state"/>（新运营页统一写法）。 -->
<template>
  <span class="st-tag" :class="`st-${tone}`" :title="raw">{{ text }}</span>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { bucketLabel, bucketTone } from '../utils/status'
const props = defineProps<{ bucket?: string, state?: string, status?: string,
  map?: Record<string, string> }>()
// RT-01：优先级 bucket > state > status（新页面统一 :status 传参不再落 "—"）
const raw = computed(() => props.bucket ?? props.state ?? props.status ?? '')
const text = computed(() => {
  const v = raw.value
  if (props.map) return props.map[v] ?? v
  return bucketLabel(v)
})
const tone = computed(() => bucketTone(raw.value))
</script>
<style scoped>
.st-tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; border: 1px solid transparent; }
.st-success { background: var(--c-success-weak); color: var(--c-success); }
.st-warning { background: var(--c-warning-weak); color: var(--c-warning); }
.st-danger { background: var(--c-danger-weak); color: var(--c-danger); }
.st-info { background: var(--c-info-weak); color: var(--c-info); }
.st-mock { background: var(--c-mock-weak); color: var(--c-mock); }
.st-muted { background: #eceff3; color: var(--c-text-2); }
</style>
