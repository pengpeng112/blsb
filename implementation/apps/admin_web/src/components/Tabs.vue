<!-- 页签：v-model 当前 tab；syncQuery 时与路由 query 同步（可收藏/刷新保持）。
用法（RT-01 起两种写法等价）：
  包裹写法：<Tabs v-model="tab" :tabs="tabs"><div v-if="tab==='a'">…</div></Tabs>
  并列写法：<Tabs v-model="tab" :tabs="tabs" sync-query/> <div v-if="tab==='a'">…</div>
关键修复：必须有默认 <slot/>——否则包裹写法的页签内容完全不渲染（TargetDetail
7 个页签空白 P0 的根因）。 -->
<template>
  <div class="tabs" role="tablist">
    <button v-for="t in tabs" :key="t.key" role="tab"
      class="tab" :class="{ active: t.key === modelValue }"
      :aria-selected="t.key === modelValue"
      @click="switchTo(t.key)">
      {{ t.label }}<span v-if="t.count !== undefined" class="tab-count">{{ t.count }}</span>
    </button>
  </div>
  <div class="tab-body"><slot /></div>
</template>
<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
export interface TabItem { key: string, label: string, count?: number }
const props = withDefaults(defineProps<{ modelValue: string, tabs: TabItem[],
  syncQuery?: boolean }>(), { syncQuery: false })
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>()
const route = useRoute()
const router = useRouter()
function switchTo(k: string) {
  emit('update:modelValue', k)
  if (props.syncQuery) router.replace({ query: { ...route.query, tab: k } })
}
</script>
<style scoped>
.tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--c-border); margin-bottom: 14px; flex-wrap: wrap; }
.tab { background: none; border: 0; border-bottom: 2px solid transparent; border-radius: 0; /* RT-01：免疫全局 button 圆角污染，激活底边保持平直 */ color: var(--c-text-2); padding: 8px 14px; cursor: pointer; font-size: 13px; display: flex; gap: 6px; align-items: center; }
.tab:hover { color: var(--c-text); }
.tab.active { color: var(--c-primary); border-bottom-color: var(--c-primary); }
.tab-count { background: var(--c-primary-weak); color: var(--c-primary); border-radius: 8px; padding: 0 6px; font-size: 11px; }
.tab-body { min-height: 0; }
</style>
