// RT-01 路由表构建（纯逻辑，无 window/localStorage 依赖，node --test 可直接
// import 做完整性断言）。index.ts 只负责 createRouter 与守卫。
// 修复两处 P0 断路：
//  A. /system/users 这类"路径≠组件名"的菜单项显式用 view 查表（原先
//     map['system/users'] 为 undefined → 点击白屏）；
//  B. 凡 path 以 /targets/ 开头的菜单项（如传染病上报）一律不重复注册静态
//     路由——vue-router 静态路由优先级高于动态段，原先生成了一条
//     component: undefined 的静态路由劫持 /targets/:code，进详情即白屏。
import type { RouteRecordRaw } from 'vue-router'
import { MENU } from './menu'

export const VIEW_MAP: Record<string, () => Promise<any>> = {
  overview: () => import('../views/Overview.vue'),
  cases: () => import('../views/Cases.vue'),
  quality: () => import('../views/Quality.vue'),
  review: () => import('../views/Review.vue'),
  devices: () => import('../views/Devices.vue'),
  files: () => import('../views/Files.vue'),
  connectors: () => import('../views/Connectors.vue'),
  mapping: () => import('../views/Mapping.vue'),
  lineage: () => import('../views/Lineage.vue'),
  workflows: () => import('../views/Workflows.vue'),
  pack: () => import('../views/Pack.vue'),
  submissions: () => import('../views/Submissions.vue'),
  publications: () => import('../views/Publications.vue'),
  reconciliations: () => import('../views/Reconciliations.vue'),
  metrics: () => import('../views/Metrics.vue'),
  drugtrace: () => import('../views/DrugTrace.vue'),
  infectious: () => import('../views/InfectiousMonitor.vue'),
  storage: () => import('../views/Storage.vue'),
  blockers: () => import('../views/Blockers.vue'),
  dashboard: () => import('../views/Dashboard.vue'),
  targets: () => import('../views/Targets.vue'),
  ledger: () => import('../views/Ledger.vue'),
  schedules: () => import('../views/Schedules.vue'),
  alerts: () => import('../views/Alerts.vue'),
  users: () => import('../views/Users.vue'),
  // RT-01：升级版审计检索占用正位 /audit（旧简陋页退出路由）。
  // 键名与 menu.ts 的 view 字段严格一致（硬断言会抓不匹配）。
  auditSearch: () => import('../views/AuditSearch.vue'),
}

/** 由菜单构建路由表；构造完成即做非空组件硬断言（未来写错路径在
 启动/测试阶段立刻报错，而不是线上白屏）。 */
export function buildRoutes(): RouteRecordRaw[] {
  const menuRoutes: RouteRecordRaw[] = MENU.flatMap(g => g.items)
    // /targets/* 已被下方动态路由 /targets/:code 覆盖：跳过静态重复注册。
    .filter(it => !it.path.startsWith('/targets/'))
    .map(it => ({
      path: it.path,
      name: it.path.replace(/^\//, ''),
      component: VIEW_MAP[it.view ?? it.path.replace(/^\//, '')],
      meta: { label: it.label, roles: it.roles ?? [], mock: !!it.mock },
    }))
  const routes: RouteRecordRaw[] = [
    { path: '/', redirect: '/dashboard' },
    // 业务详情动态段（必须在 MENU 静态项之后声明也安全：静态 /targets/* 已被过滤）
    { path: '/targets/:code', name: 'target-detail',
      component: () => import('../views/TargetDetail.vue'),
      meta: { label: '业务详情' } },
    ...menuRoutes,
    // RT-01：/audit2 旧书签 → 升级版审计检索正位 /audit。
    { path: '/audit2', redirect: '/audit' },
    { path: '/403', name: 'forbidden',
      component: () => import('../views/Forbidden.vue'),
      meta: { label: '无权访问' } },
    { path: '/:pathMatch(.*)*', name: 'not-found',
      component: () => import('../views/NotFound.vue'),
      meta: { label: '页面不存在' } },
  ]
  for (const r of routes) {
    if ((r as any).redirect) continue
    if (!r.component) {
      throw new Error(`Route missing component: ${r.path}（检查 menu.ts 的 view/path 与 VIEW_MAP 键名）`)
    }
  }
  return routes
}
