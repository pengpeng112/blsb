<!-- 错误态（带可选重试）。用法：<ErrorState :message="err" @retry="load"/>
RT-01：① compact/small 支持卡片内紧凑展示；② 父组件未绑 @retry 时自动隐藏
重试按钮（避免点了没反应）。 -->
<template>
  <div class="error-state" role="alert" :class="{ compact, small }">
    <span v-if="!small" class="es-symbol">⚠</span>
    <p class="es-msg">{{ message }}</p>
    <button v-if="showRetry" class="ghost" @click="$emit('retry')">重试</button>
  </div>
</template>
<script setup lang="ts">
import { computed, useSlots } from 'vue'
withDefaults(defineProps<{ message?: string, compact?: boolean,
  small?: boolean }>(), { message: '加载失败', compact: false, small: false })
defineEmits<{ (e: 'retry'): void }>()
const slots = useSlots()
const showRetry = computed(() => !!slots.retry)
</script>
<style scoped>
.error-state { display: grid; place-items: center; gap: 8px; padding: 28px 12px; background: var(--c-danger-weak); border: 1px solid var(--c-danger); border-radius: var(--radius-lg); }
.error-state.compact { padding: 14px 10px; }
.error-state.small { padding: 10px 8px; border-radius: var(--radius); }
.es-symbol { font-size: 22px; }
.error-state.small .es-symbol { font-size: 16px; }
.es-msg { margin: 0; color: var(--c-danger); font-size: 13px; word-break: break-all; }
.error-state.small .es-msg { font-size: 12px; }
</style>
