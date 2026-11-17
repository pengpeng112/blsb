<!-- 404：未知路径兜底。RT-01：返回首页指向 /dashboard。 -->
<template>
  <div class="http-page">
    <h2>404 · 页面不存在</h2>
    <p class="muted">地址可能已失效或拼写有误。</p>
    <router-link to="/dashboard"><button class="ghost">返回首页</button></router-link>
  </div>
</template>
<style scoped>
.http-page { display: grid; place-items: center; gap: 10px; padding: 60px 12px; }
</style>
