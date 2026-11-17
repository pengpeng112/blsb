// LT-02 菜单角色过滤（纯 TS；menu.ts 的菜单数据结构在此定义形状）。
export interface MenuItem {
  path: string
  label: string
  roles?: string[]        // 空/缺省 = 所有登录用户
  mock?: boolean          // 显示 Mock 角标
  /** RT-01：显式指定视图组件名（缺省按 path 推导）；用于 /system/users 这类
   路径与文件名不一致的菜单项，杜绝 component: undefined 白屏。 */
  view?: string
  /** RT-01：按用户 target_scopes 过滤（如 'drug_trace_alihealth'）；
   声明了 targetScope 且用户 scopes 不含它（也不含 '*'）则隐藏。 */
  targetScope?: string
}
export interface MenuGroup {
  key: string
  label: string
  items: MenuItem[]
}

/**
 * platform_admin 看全部；其余角色按 item.roles 交集过滤；
 * 过滤后空分组隐藏。roles 参数为当前用户角色数组。
 * RT-01：新增可选 scopes（用户 target_scopes；platform_admin 或含 '*' 不限制）
 * ——声明了 targetScope 的菜单项（如各业务入口）不在授权范围内则隐藏。
 */
export function filterMenuByRoles(menu: MenuGroup[], roles: string[],
  scopes?: string[]): MenuGroup[] {
  const roleArr = roles ?? []
  const isAdmin = roleArr.includes('platform_admin')
  const scopeArr = scopes ?? null
  const scopeUnlimited = isAdmin || scopeArr === null || scopeArr.includes('*')
  const out: MenuGroup[] = []
  for (const g of menu) {
    const items = g.items.filter(it => {
      if (!it.roles || it.roles.length === 0) return true
      if (isAdmin) return true
      return it.roles.some(r => roleArr.includes(r))
    }).filter(it => {
      if (!it.targetScope || scopeUnlimited) return true
      return scopeArr.includes(it.targetScope)
    })
    if (items.length > 0) out.push({ ...g, items })
  }
  return out
}
