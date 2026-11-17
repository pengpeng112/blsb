<!-- 过滤条：slot 放控件；回车触发查询；右侧查询/重置按钮。
用法：<FilterBar @search="load" @reset="reset"><input v-model="kw" placeholder="关键词"/></FilterBar> -->
<template>
  <form class="filter-bar" @submit.prevent="$emit('search')">
    <div class="fb-fields"><slot /></div>
    <div class="fb-actions">
      <button type="submit" :disabled="busy">查询</button>
      <button type="button" class="ghost" @click="$emit('reset')">重置</button>
    </div>
  </form>
</template>
<script setup lang="ts">
defineProps<{ busy?: boolean }>()
defineEmits<{ (e: 'search'): void, (e: 'reset'): void }>()
</script>
<style scoped>
.filter-bar { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; background: var(--c-surface); border: 1px solid var(--c-border); border-radius: var(--radius-lg); padding: 10px 12px; margin-bottom: 12px; }
.fb-fields { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; flex: 1; }
.fb-fields :deep(label) { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--c-text-2); }
.fb-actions { display: flex; gap: 8px; }
</style>
