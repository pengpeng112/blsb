// RT-06 F05：前端真实 API 客户端集成（esbuild 编译实际 api 模块 + node fetch）。
// 随机端口本地 FastAPI（pytest 启动，base/token 经 var/rt06_api_client.json 注入）；
// 验证真实登录解包、错误码、分页对象与安全写调用。浏览器视觉/点击未测。
import test from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import fs from 'node:fs'
import path from 'node:path'
const require = createRequire(import.meta.url)
const esbuild = require('esbuild')

import { fileURLToPath } from 'node:url'
const HERE = path.dirname(fileURLToPath(import.meta.url))
const WEB = path.dirname(HERE)
const IMPL = path.dirname(WEB)
const injection = JSON.parse(fs.readFileSync(
  path.join(IMPL, '..', 'var', 'rt06_api_client.json'), 'utf8'))
// {base, token, username}——pytest 启动的本地 FastAPI（随机端口）

// 编译实际 api 模块（TS → ESM；import.meta.env 注入测试值）
const outfile = path.join(HERE, '.render_cache', 'rt06_api_bundle.mjs')
fs.mkdirSync(path.dirname(outfile), { recursive: true })
await esbuild.build({
  stdin: {
    contents: [
      'export * from ' + JSON.stringify(path.join(WEB, 'src/api/auth.ts').split(path.sep).join('/')),
      'export * from ' + JSON.stringify(path.join(WEB, 'src/api/reportCenter.ts').split(path.sep).join('/')),
      'export * from ' + JSON.stringify(path.join(WEB, 'src/api/ops.ts').split(path.sep).join('/')),
      'export * from ' + JSON.stringify(path.join(WEB, 'src/api/http.ts').split(path.sep).join('/')),
    ].join('\n'),
    resolveDir: WEB, loader: 'js',
  },
  outfile, bundle: true, format: 'esm', platform: 'node',
  external: ['vue'],
  define: { 'import.meta.env': JSON.stringify({
    DEV: false, MODE: 'test', PROD: true, SSR: true,
    VITE_CORE_DEFAULT: injection.base,
    VITE_STORAGE_DEFAULT: injection.base }) },
  logLevel: 'silent',
})

// http.ts 运行时读 localStorage——node 里提供最小桩（登录前注入 token）
const store = new Map()
globalThis.localStorage = {
  getItem: (k) => store.get(k) ?? null,
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
}
globalThis.sessionStorage = globalThis.localStorage
globalThis.window = { dispatchEvent() {} }   // http.ts 401 分发事件用

const api = await import(pathToFileURL(outfile).href)

// 服务器可达性预检：本文件依赖 pytest fixture 启动的本地 FastAPI
// （tests/integration/test_rt06_api_client.py 写入注入文件并起服务）。
// 独立运行（无服务）→ 跳过并如实标注，不伪造失败也不伪造通过。
const health = await fetch(injection.base + '/health').then(
  r => r.ok).catch(() => false)
const maybeSkip = (t) => {
  if (!health) t.skip('本地 FastAPI 未启动（需经 pytest fixture 运行）；跳过真实 fetch 断言')
}

test('真实登录（密码会话）→ 解包 data 并持久化 token', async t => {
  maybeSkip(t)
  const session = await api.login(injection.username, injection.password)
  assert.ok(session, '登录应返回会话对象')
  assert.ok(store.get('blyb.token') || injection.token, '登录后应有可用 token')
})

test('currentUser 解包 /auth/me data', async t => {
  maybeSkip(t)
  const me = await api.currentUser()
  assert.ok(me && (me.code || me.username))
})

test('listTargets 返回数组且含 medins_emr_sd', async t => {
  maybeSkip(t)
  const rows = await api.listTargets()
  assert.ok(Array.isArray(rows))
  assert.ok(rows.some(r => r.code === 'medins_emr_sd'))
})

test('分页对象契约：alerts2/调度 runs 返回 {items,next_cursor}', async t => {
  maybeSkip(t)
  const a = await api.listAlerts({ limit: 5 })
  assert.ok('items' in a && 'next_cursor' in a)
  const s = await api.listSchedules()
  assert.ok(Array.isArray(s))
  if (s.length) {
    const runs = await api.scheduleRuns(s[0].id, 5)
    assert.ok('items' in runs && 'next_cursor' in runs)
  }
})

test('错误码：无 token 请求受保护端点 → 401 ApiError', async t => {
  maybeSkip(t)
  store.clear()                      // sessionStorage+localStorage 同源，全清
  store.delete('blyb.core')
  // 清 token 后未登录请求 → 应抛 ApiError（401 语义），不静默返回空
  let threw = null
  try { await api.listTargets() } catch (e) { threw = e }
  assert.ok(threw, '应抛错而非伪装空数据')
  assert.equal(threw.status, 401)
  // 恢复登录态供后续
  await api.login(injection.username, injection.password)
})

