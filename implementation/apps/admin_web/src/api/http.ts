// LT-02 类型化 API 层：统一请求/错误/取消（纯 fetch，无新依赖）。
// 后端地址读取逻辑自旧 api.ts 原样迁入（C-24：每次调用读取；DEV 走 vite 代理）。
const CORE_DEFAULT: string = import.meta.env.VITE_CORE_DEFAULT ?? 'http://127.0.0.1:8000'
const STORAGE_DEFAULT: string = import.meta.env.VITE_STORAGE_DEFAULT ?? 'http://127.0.0.1:8001'

export function coreBase(): string {
  return import.meta.env.DEV ? '/core' : (localStorage.getItem('blyb.core') || CORE_DEFAULT)
}
export function storageBase(): string {
  return import.meta.env.DEV ? '/storage' : (localStorage.getItem('blyb.storage') || STORAGE_DEFAULT)
}

export function token(): string {
  return sessionStorage.getItem('blyb.token') || localStorage.getItem('blyb.token') || ''
}
export function setToken(t: string) {
  localStorage.removeItem('blyb.token')
  sessionStorage.setItem('blyb.token', t)
}
export function clearLogin() {
  sessionStorage.removeItem('blyb.token')
  for (const key of ['blyb.token', 'blyb.roles', 'blyb.who', 'blyb.scopes']) localStorage.removeItem(key)
}

export function savedBackends(): { core: string, storage: string } {
  return {
    core: localStorage.getItem('blyb.core') || CORE_DEFAULT,
    storage: localStorage.getItem('blyb.storage') || STORAGE_DEFAULT,
  }
}
export function setBackends(core: string, storage: string) {
  localStorage.setItem('blyb.core', core.trim().replace(/\/+$/, ''))
  localStorage.setItem('blyb.storage', storage.trim().replace(/\/+$/, ''))
}

/** 统一错误对象：status/code/message/requestId（页面按 code 分支展示）。
 RT-01：保留 .data=后端原始响应 JSON（与旧 api.ts 行为一致），DrugTrace 等
 旧页面读取 e.data?.error?.message 仍能拿到详细错误文案。 */
export class ApiError extends Error {
  status: number
  code: string
  requestId: string
  data: any
  constructor(status: number, code: string, message: string, requestId: string,
    data?: any) {
    super(message)
    this.status = status
    this.code = code
    this.requestId = requestId
    this.data = data
  }
}

export interface RequestOptions {
  body?: any
  query?: Record<string, string | number | undefined | null>
  idemKey?: string
  signal?: AbortSignal
  extraHeaders?: Record<string, string>
}

function buildPath(path: string, query?: RequestOptions['query']): string {
  if (!query) return path
  const parts: string[] = []
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
  }
  return parts.length ? `${path}${path.includes('?') ? '&' : '?'}${parts.join('&')}` : path
}

async function request(base: string, method: string, path: string,
                       opts: RequestOptions = {}): Promise<any> {
  const r = await fetch(base + buildPath(path, opts.query), {
    method,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token()}`,
      ...(opts.idemKey ? { 'Idempotency-Key': opts.idemKey } : {}),
      ...(opts.extraHeaders || {}),
    },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    signal: opts.signal,
  })
  const payload = await r.json().catch(() => ({} as any))
  if (!r.ok) {
    if (r.status === 401) {
      clearLogin()
      window.dispatchEvent(new Event('blyb:unauthorized'))
    }
    throw new ApiError(r.status,
      payload?.error?.code ?? 'HTTP_ERROR',
      payload?.error?.message || r.statusText,
      payload?.request_id ?? '',
      payload)
  }
  return payload.data
}

export const core = {
  get: (p: string, query?: RequestOptions['query'], signal?: AbortSignal) =>
    request(coreBase(), 'GET', p, { query, signal }),
  // PT-02：可选 query（第 5 参；既有调用零改动）——路由审计要求 path 模板串
  // 不内联查询串（buildPath 统一拼接）
  post: (p: string, body?: any, idemKey?: string,
         extraHeaders?: Record<string, string>, query?: RequestOptions['query']) =>
    request(coreBase(), 'POST', p, { body, idemKey, extraHeaders, query }),
  put: (p: string, body?: any) => request(coreBase(), 'PUT', p, { body }),
  del: (p: string) => request(coreBase(), 'DELETE', p),
}
export const storage = {
  get: (p: string, signal?: AbortSignal) => request(storageBase(), 'GET', p, { signal }),
  post: (p: string, body?: any, idemKey?: string) =>
    request(storageBase(), 'POST', p, { body, idemKey }),
}
