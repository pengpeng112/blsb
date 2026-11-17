// LT-02 路由：路由表构建在 routes.ts（可单测硬断言）；此处 createRouter + 守卫。
import { createRouter, createWebHashHistory } from 'vue-router'
import { buildRoutes, VIEW_MAP } from './routes'
import { MENU } from './menu'

export { buildRoutes, VIEW_MAP }

export function currentRoles(): string[] {
  try { return JSON.parse(localStorage.getItem('blyb.roles') || '[]') as string[] }
  catch { return [] }
}

export function currentScopes(): string[] {
  try { return JSON.parse(localStorage.getItem('blyb.scopes') || '[]') as string[] }
  catch { return [] }
}

export const router = createRouter({ history: createWebHashHistory(),
  routes: buildRoutes() })

// 守卫：角色不足→/403；未登录（无 token）→ 回 /dashboard（App.vue 对无登录态
// 渲染登录界面覆盖内容区，等价旧 v-if 行为；任务书允许该形态，守卫必须生效）。
// RT-01：403/404 一律可回首页 /dashboard；菜单声明的 targetScope 同样校验
// （与侧栏过滤同一规则，防直链绕过）。
router.beforeEach(to => {
  if (to.name === 'not-found' || to.name === 'forbidden') return true
  const roles = currentRoles()
  const need = (to.meta?.roles as string[]) ?? []
  const hasToken = !!(sessionStorage.getItem('blyb.token') ||
    localStorage.getItem('blyb.token'))
  if (!hasToken) {
    return to.path === '/dashboard' ? true : '/dashboard'
  }
  if (need.length > 0 && !roles.includes('platform_admin') &&
      !need.some(r => roles.includes(r))) {
    return '/403'
  }
  const item = MENU.flatMap(g => g.items).find(it => it.path === to.path)
  if (item?.targetScope && !roles.includes('platform_admin')) {
    const scopes = currentScopes()
    if (!(scopes.includes('*') || scopes.includes(item.targetScope))) {
      return '/403'
    }
  }
  return true
})
