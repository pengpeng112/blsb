// LT-02 认证端点（自旧 api.ts authenticate 逻辑原样迁入）。
// RT-01：登录/me 成功后同步持久化 roles 与 target_scopes（scopes 为 null
// 表示不受限，统一存 ['*']），侧栏菜单按范围过滤依赖这两份本地镜像。
import { core, token, setToken, clearLogin } from './http'
import type { SessionInfo } from './types'

/** 把会话身份（roles + target_scopes）写入 localStorage（blyb.roles/blyb.scopes）。 */
export function persistIdentity(s: SessionInfo): void {
  localStorage.setItem('blyb.roles', JSON.stringify(s.roles || []))
  const sc = (s as any).target_scopes
  const v = Array.isArray(sc) ? sc : ['*']   // null/undefined=不受限 → ['*']
  localStorage.setItem('blyb.scopes', JSON.stringify(v))
}

async function authenticate(body: Record<string, string>): Promise<SessionInfo> {
  const r = await fetch(coreBase0() + '/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await r.json().catch(() => ({} as any))
  if (!r.ok) {
    throw new Error(r.status === 429
      ? '登录尝试过于频繁，请稍后再试'
      : (data?.error?.message || '登录失败'))
  }
  const subj = data.data as SessionInfo
  setToken(subj.access_token || body.token)
  persistIdentity(subj)
  return subj
}

// 避免顶层循环依赖（auth ← http 已导入）；登录请求不携带 token，直接读 base。
function coreBase0(): string {
  // DEV 走 vite 代理；构建产物读 localStorage / 注入默认（与 http.coreBase 一致）
  return import.meta.env.DEV ? '/core' : (localStorage.getItem('blyb.core') ||
    (import.meta.env.VITE_CORE_DEFAULT ?? 'http://127.0.0.1:8000'))
}

export function login(username: string, password: string) {
  return authenticate({ username, password })
}
export function loginWithToken(t: string) { return authenticate({ token: t }) }
export function currentUser() { return core.get('/api/v1/auth/me') }
export async function logout() {
  try { if (token()) await core.post('/api/v1/auth/logout', {}) }
  finally { clearLogin() }
}
export function changePassword(current_password: string, new_password: string) {
  return core.post('/api/v1/auth/password', { current_password, new_password })
}
