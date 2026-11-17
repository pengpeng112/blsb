<!-- 院内时间文本（固定 UTC+8，悬停显示原始值）。
用法：<TimeText :value="row.updated_at"/> -->
<template>
  <span class="tt" :title="value ?? ''">{{ text }}</span>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { formatHospitalTime } from '../utils/time'
const props = defineProps<{ value?: string | null }>()
const text = computed(() => formatHospitalTime(props.value))
</script>
<style scoped>
.tt { font-variant-numeric: tabular-nums; }
</style>
