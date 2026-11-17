<!-- 字节文本（KB/MB/GB，悬停显示原始字节数；v0.4 §7.5）。
用法：<BytesText :value="blob.size_bytes"/> -->
<template>
  <span class="bt" :title="value === null || value === undefined ? '' : String(value)">{{ text }}</span>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { formatBytes } from '../utils/bytes'
const props = defineProps<{ value?: number | null }>()
const text = computed(() => formatBytes(props.value))
</script>
<style scoped>
.bt { font-variant-numeric: tabular-nums; }
</style>
