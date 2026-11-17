<!-- 403：角色不足（守卫跳转目标）。RT-01：返回首页指向 /dashboard。 -->
<template>
  <div class="http-page">
    <h2>403 · 无权访问</h2>
    <p class="muted">当前角色无权查看该页面。如需权限请联系平台管理员。</p>
    <router-link to="/dashboard"><button class="ghost">返回首页</button></router-link>
  </div>
</template>
<style scoped>
.http-page { display: grid; place-items: center; gap: 10px; padding: 60px 12px; }
</style>
