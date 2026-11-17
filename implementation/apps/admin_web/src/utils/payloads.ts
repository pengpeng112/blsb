// RT-01 前后端契约载荷构造（纯 TS，node --test 直测）。
// 页面表单字段名与后端字段名不一致的地方集中在这里映射，避免再次静默丢字段。
import { hospitalLocalInputToUtcIso } from './time'

export interface NewUserForm {
  username: string
  display_name: string
  password: string
  roles: string[]
  scopes: string[]
}

/** 新建用户：表单 scopes → 后端 target_scopes（system_routes 只读 target_scopes）。 */
export function buildCreateUserPayload(n: NewUserForm) {
  return {
    username: n.username,
    password: n.password,
    display_name: n.display_name,
    roles: n.roles.length ? n.roles : ['operator'],
    target_scopes: [...n.scopes],
  }
}

export interface AuditFilterForm {
  actor: string
  action: string
  actionPrefix: string
  target: string
  atFrom: string      // datetime-local（院内东八区）
  atTo: string
}

/** 审计筛选：空值不传；时间由院内本地时间换算为 UTC（见 time.ts）。 */
export function buildAuditParams(f: AuditFilterForm) {
  return {
    actor: f.actor || undefined,
    action: f.action || undefined,
    action_prefix: f.actionPrefix || undefined,
    target: f.target || undefined,
    at_from: hospitalLocalInputToUtcIso(f.atFrom),
    at_to: hospitalLocalInputToUtcIso(f.atTo),
  }
}
