<!-- 骨架加载块。用法：<LoadingBlock v-if="loading"/> -->
<template>
  <div class="loading-block" aria-busy="true">
    <span class="sk" style="width: 40%"></span>
    <span class="sk" style="width: 92%"></span>
    <span class="sk" style="width: 78%"></span>
    <span class="sk" style="width: 85%"></span>
  </div>
</template>
<style scoped>
.loading-block { display: flex; flex-direction: column; gap: 10px; padding: 18px; background: var(--c-surface); border: 1px solid var(--c-border); border-radius: var(--radius-lg); }
.sk { display: block; height: 14px; border-radius: 6px; background: linear-gradient(90deg, #eef1f5 25%, #e3e8ee 50%, #eef1f5 75%); background-size: 200% 100%; animation: sk 1.2s infinite; }
@keyframes sk { from { background-position: 200% 0; } to { background-position: -200% 0; } }
</style>
