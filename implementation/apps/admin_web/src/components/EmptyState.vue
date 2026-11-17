<!-- 空态。用法：<EmptyState text="暂无上报业务"/> -->
<template>
  <div class="empty-state">
    <span class="empty-symbol">∅</span>
    <p>{{ text }}</p>
    <slot />
  </div>
</template>
<script setup lang="ts">
withDefaults(defineProps<{ text?: string }>(), { text: '暂无数据' })
</script>
<style scoped>
.empty-state { display: grid; place-items: center; gap: 6px; padding: 32px 12px; color: var(--c-text-3); background: var(--c-surface); border: 1px dashed var(--c-border); border-radius: var(--radius-lg); }
.empty-symbol { font-size: 26px; opacity: .5; }
.empty-state p { margin: 0; font-size: 13px; }
</style>
