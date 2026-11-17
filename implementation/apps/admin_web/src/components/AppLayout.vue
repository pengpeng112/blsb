<!-- LT-02 应用外壳：侧栏+顶栏+面包屑+内容区。窄屏（<900px）侧栏抽屉化。
用法：<AppLayout :menu="groups" :user="who" :roles="roles"
  @logout="fn" @change-password="fn"><router-view/></AppLayout> -->
<template>
  <div class="app-shell">
    <aside class="app-aside" :class="{ collapsed, 'drawer-open': drawer }">
      <div class="app-brand"><h1>归集上报平台</h1>
        <button class="ghost collapse-btn" @click="toggleCollapse" :title="collapsed ? '展开' : '折叠'">{{ collapsed ? '»' : '«' }}</button>
      </div>
      <nav class="app-nav">
        <template v-for="g in visibleMenu" :key="g.key">
          <div class="menu-group-label">{{ g.label }}</div>
          <router-link v-for="it in g.items" :key="it.path" :to="it.path"
            :title="it.label">
            <span class="menu-item-label">{{ it.label }}</span>
            <span v-if="it.mock" class="mock-badge" title="Mock 数据">Mock</span>
          </router-link>
        </template>
      </nav>
      <div class="side-footer">
        <span class="who">{{ user }}</span>
        <span class="side-actions">
          <button v-if="passwordSession" @click="$emit('change-password')">改密</button>
          <button @click="$emit('logout')">退出</button>
        </span>
      </div>
    </aside>
    <div v-if="drawer" class="drawer-mask" @click="drawer = false"></div>
    <div class="app-main">
      <header class="app-topbar">
        <button class="ghost burger" @click="drawer = !drawer">☰</button>
        <nav class="crumbs" aria-label="面包屑">
          <span v-for="(c, i) in crumbs" :key="i" class="crumb">
            <router-link v-if="c.to && i < crumbs.length - 1" :to="c.to">{{ c.label }}</router-link>
            <span v-else class="crumb-current">{{ c.label }}</span>
            <span v-if="i < crumbs.length - 1" class="crumb-sep">/</span>
          </span>
        </nav>
        <span class="topbar-spacer"></span>
        <span v-if="isDev" class="env-badge" title="import.meta.env.DEV">开发环境</span>
        <span class="roles" :title="roles.join(', ')">{{ roleNames }}</span>
        <span class="who-top">{{ user }}</span>
      </header>
      <div class="app-content"><slot /></div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import type { MenuGroup } from '../utils/menu'
import { filterMenuByRoles } from '../utils/menu'

const props = defineProps<{ menu: MenuGroup[], user: string, roles: string[],
  scopes?: string[], passwordSession?: boolean }>()
defineEmits<{ (e: 'logout'): void, (e: 'change-password'): void }>()

const collapsed = ref(localStorage.getItem('blyb.menu.collapsed') === '1')
const drawer = ref(false)
const route = useRoute()
const isDev = !!import.meta.env.DEV

const ROLE_NAMES: Record<string, string> = {
  platform_admin: '平台管理员', config_editor: '配置编辑',
  business_reviewer: '业务复核', operator: '运维操作',
  auditor: '审计员', service: '服务账号',
}
const roleNames = computed(() =>
  (props.roles ?? []).map(r => ROLE_NAMES[r] ?? r).join(' / ') || '—')
const visibleMenu = computed(() => filterMenuByRoles(
  props.menu, props.roles ?? [], props.scopes))
const crumbs = computed(() => {
  const parts: { label: string, to?: string }[] = []
  const path = String(route.path ?? '/')
  for (const g of visibleMenu.value) {
    for (const it of g.items) {
      if (it.path === path) parts.push({ label: g.label, to: undefined })
    }
  }
  parts.push({ label: String(route.meta?.label ?? route.name ?? path), to: path })
  return parts.filter(Boolean)
})
function toggleCollapse() {
  collapsed.value = !collapsed.value
  localStorage.setItem('blyb.menu.collapsed', collapsed.value ? '1' : '0')
}
watch(() => route.path, () => { drawer.value = false })
</script>
<style scoped>
.app-shell { display: flex; min-height: 100vh; }
/* RT-01：显式 box-sizing/padding——折叠到 56px 时图标居中不挤压
   （components.css 裸 aside 已加 :not(.app-aside)，此处双保险）。 */
.app-aside { width: var(--sidebar-w); background: #10233f; color: #cfe0f5; display: flex; flex-direction: column; position: sticky; top: 0; height: 100vh; overflow-y: auto; transition: width .15s; box-sizing: border-box; padding: 0 6px; }
.app-aside.collapsed { width: 56px; }
.app-aside.collapsed .menu-item-label, .app-aside.collapsed .menu-group-label, .app-aside.collapsed .app-brand h1 { display: none; }
.app-brand { display: flex; align-items: center; justify-content: space-between; padding: 12px 10px; }
.app-brand h1 { font-size: 15px; color: #fff; margin: 0; padding: 0 4px; white-space: nowrap; }
.collapse-btn { background: none; border: 1px solid #3a5a86; color: #cfe0f5; border-radius: 4px; padding: 0 6px; cursor: pointer; }
.app-nav { display: flex; flex-direction: column; gap: 2px; flex: 1; padding: 0 8px; }
.menu-group-label { color: #6f89ad; font-size: 11px; padding: 12px 6px 4px; letter-spacing: 1px; }
.app-nav a { color: #cfe0f5; text-decoration: none; padding: 7px 10px; border-radius: var(--radius); font-size: 13px; display: flex; align-items: center; gap: 6px; }
.app-nav a:hover { background: #16304f; }
.app-nav a.router-link-active { background: #1d3f70; color: #fff; }
.menu-item-label { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mock-badge { background: var(--c-mock); color: #fff; font-size: 10px; border-radius: 8px; padding: 0 6px; }
.side-footer { font-size: 12px; padding: 10px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px; border-top: 1px solid #1d3f70; }
.side-footer button { background: none; border: 1px solid #3a5a86; color: #cfe0f5; border-radius: 4px; padding: 2px 8px; cursor: pointer; }
.who { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 96px; }
.app-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.app-topbar { height: var(--topbar-h); background: var(--c-surface); border-bottom: 1px solid var(--c-border); display: flex; align-items: center; gap: 12px; padding: 0 16px; position: sticky; top: 0; z-index: 20; }
.burger { display: none; }
.crumbs { font-size: 13px; color: var(--c-text-2); display: flex; gap: 4px; align-items: center; }
.crumb-sep { color: var(--c-text-3); }
.crumb-current { color: var(--c-text); }
.env-badge { background: var(--c-warning-weak); color: var(--c-warning); font-size: 12px; border-radius: 8px; padding: 1px 8px; }
.roles { font-size: 12px; color: var(--c-text-2); }
.who-top { font-size: 13px; }
.app-content { flex: 1; padding: 18px 22px; overflow: auto; }
.drawer-mask { display: none; }
@media (max-width: 900px) {
  .app-aside { position: fixed; left: 0; top: 0; z-index: 60; transform: translateX(-100%); transition: transform .2s; }
  .app-aside.drawer-open { transform: none; }
  .drawer-mask { display: block; position: fixed; inset: 0; background: #0006; z-index: 50; }
  .burger { display: inline-block; }
  .app-content { padding: 12px; }
}
</style>
