// 后端地址每次调用时读取（C-24）：打包版登录页改地址后无需刷新整个应用。
// DEV 走 vite 代理（/core /storage）；构建产物读 localStorage 的 blyb.core / blyb.storage。
// 同源发行构建（DEP-A07/A12）在构建期注入 VITE_CORE_DEFAULT=''、VITE_STORAGE_DEFAULT='/storage'，
// 由 nginx 剥离 /storage 前缀；未注入时保持旧的 127.0.0.1 分离默认，旧手动地址优先级不变。
const CORE_DEFAULT: string = import.meta.env.VITE_CORE_DEFAULT ?? 'http://127.0.0.1:8000'
const STORAGE_DEFAULT: string = import.meta.env.VITE_STORAGE_DEFAULT ?? 'http://127.0.0.1:8001'
function coreBase(): string {
  return import.meta.env.DEV ? '/core' : (localStorage.getItem('blyb.core') || CORE_DEFAULT)
}
function storageBase(): string {
  return import.meta.env.DEV ? '/storage' : (localStorage.getItem('blyb.storage') || STORAGE_DEFAULT)
}

export function token(): string { return localStorage.getItem('blyb.token') || '' }
export function setToken(t: string) { localStorage.setItem('blyb.token', t) }

export function savedBackends(): { core: string, storage: string } {
  return {
    core: localStorage.getItem('blyb.core') || CORE_DEFAULT,
    storage: localStorage.getItem('blyb.storage') || STORAGE_DEFAULT
  }
}

export function setBackends(core: string, storage: string) {
  localStorage.setItem('blyb.core', core.trim().replace(/\/+$/, ''))
  localStorage.setItem('blyb.storage', storage.trim().replace(/\/+$/, ''))
}

async function req(base: string, method: string, path: string, body?: any, idemKey?: string, extraHeaders?: Record<string, string>) {
  const r = await fetch(base + path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token()}`,
      ...(idemKey ? { 'Idempotency-Key': idemKey } : {}),
      ...(extraHeaders || {})
    },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw Object.assign(new Error(data?.error?.message || r.statusText), { data })
  return data.data
}

export const api = {
  coreGet: (p: string) => req(coreBase(), 'GET', p),
  corePost: (p: string, b?: any, idemKey?: string, extraHeaders?: Record<string, string>) => req(coreBase(), 'POST', p, b, idemKey, extraHeaders),
  storageGet: (p: string) => req(storageBase(), 'GET', p),
  storagePost: (p: string, b?: any, idemKey?: string) => req(storageBase(), 'POST', p, b, idemKey),
}

export async function login(t: string) {
  const r = await fetch(coreBase() + '/api/v1/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: t })
  })
  if (!r.ok) throw new Error('登录失败')
  setToken(t)
  const subj = (await r.json()).data
  localStorage.setItem('blyb.roles', JSON.stringify(subj.roles || []))
  return subj
}
