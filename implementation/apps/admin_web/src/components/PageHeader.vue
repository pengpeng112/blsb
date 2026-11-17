<!-- 页头：标题/副标题/右侧动作插槽/Mock 徽标。
用法（RT-01 起两种写法等价，右侧动作都可见）：
  <PageHeader title="平台总览" subtitle="近 7 天" mock><button>刷新</button></PageHeader>
  <PageHeader title="用户与角色"><template #actions><button>新建用户</button></template></PageHeader>
关键修复：必须渲染 <slot name="actions"/>——此前只渲染默认插槽，新页面全部
用 <template #actions> 写法导致顶部按钮整体隐身（P0）。 -->
<template>
  <div class="page-header">
    <div>
      <div class="ph-title-row">
        <h2 class="ph-title">{{ title }}</h2>
        <span v-if="mock" class="ph-mock" title="该页面当前展示 Mock 数据">Mock 数据</span>
      </div>
      <div v-if="subtitle" class="ph-subtitle">{{ subtitle }}</div>
    </div>
    <div class="ph-actions"><slot name="actions" /><slot /></div>
  </div>
</template>
<script setup lang="ts">
defineProps<{ title: string, subtitle?: string, mock?: boolean }>()
</script>
<style scoped>
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.ph-title-row { display: flex; align-items: center; gap: 8px; }
.ph-title { margin: 0; font-size: 17px; }
.ph-mock { background: var(--c-mock-weak); color: var(--c-mock); border: 1px solid var(--c-mock); border-radius: 10px; font-size: 12px; padding: 1px 8px; }
.ph-subtitle { color: var(--c-text-2); font-size: 13px; margin-top: 2px; }
.ph-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
</style>
