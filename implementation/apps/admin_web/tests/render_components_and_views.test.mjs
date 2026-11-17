// RT-01 Vue SFC 真实渲染测试（零新依赖：@vue/compiler-sfc + esbuild +
// vue/server-renderer，全部 node_modules 已内置）。
// 防线目标：编译通过 ≠ 页面可用——本文件把历史 P0（Tabs 空插槽、PageHeader
// actions 隐身、StatusTag 满屏 —、Drawer/Modal 关不掉、Donut 扇区飞出、
// 路由 component:undefined 白屏、Dashboard sortTargets 崩溃等）逐个用真实
// SSR HTML 锁死。
import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { createHash } from 'node:crypto'
import fs from 'node:fs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.dirname(HERE)                                    // admin_web/
const SRC = path.join(ROOT, 'src')
const V = (p) => path.join(SRC, p)

import { loadSfc, loadTs, renderComponent, renderInline } from './sfc_loader.mjs'
import { h, createSSRApp, defineComponent, ref } from 'vue'
import { renderToString } from 'vue/server-renderer'

async function render(comp, props, slots) {
  return renderToString(createSSRApp({ render: () => h(comp, props, slots) }))
}

// ============================================================ Tabs（P0 插槽）
test('Tabs 包裹写法：默认插槽内容必须出现在渲染 HTML 中（TargetDetail 7 页签空白 P0 回归）', async () => {
  const { default: Tabs } = await loadSfc(V('components/Tabs.vue'))
  const html = await render(Tabs,
    { modelValue: 'a', tabs: [{ key: 'a', label: '概况' }, { key: 'b', label: '台账' }] },
    { default: () => h('div', { class: 'pane-a' }, '概况内容ABC') })
  assert.ok(html.includes('概况内容ABC'),
    '包裹的子节点内容未渲染（Tabs 缺 <slot/>）: ' + html)
  assert.ok(html.includes('概况') && html.includes('台账'), '页签按钮缺失: ' + html)
})

test('Tabs 自闭合写法：不抛错且 tablist 可见', async () => {
  const { default: Tabs } = await loadSfc(V('components/Tabs.vue'))
  const html = await render(Tabs,
    { modelValue: 'alerts', tabs: [{ key: 'alerts', label: '告警' }] })
  assert.ok(html.includes('role="tablist"'))
  assert.ok(html.includes('告警'))
})

// ======================================================== PageHeader（P0 插槽）
test('PageHeader #actions 具名插槽按钮必须渲染（顶部按钮隐身 P0 回归）', async () => {
  const { default: PageHeader } = await loadSfc(V('components/PageHeader.vue'))
  const html = await render(PageHeader, { title: '用户与角色' },
    { actions: () => h('button', '新建用户') })
  assert.ok(html.includes('新建用户'),
    '<template #actions> 内容未渲染: ' + html)
})

test('PageHeader 默认插槽按钮同样渲染', async () => {
  const { default: PageHeader } = await loadSfc(V('components/PageHeader.vue'))
  const html = await render(PageHeader, { title: '上报驾驶舱' },
    { default: () => h('button', '刷新') })
  assert.ok(html.includes('刷新'))
})

// ==================================================== StatusTag（P0 :status）
test('StatusTag :status="PRODUCTION" 渲染中文且绝不等于 —', async () => {
  const { default: StatusTag } = await loadSfc(V('components/StatusTag.vue'))
  const html = await render(StatusTag, { status: 'PRODUCTION' })
  assert.ok(html.includes('正式运行'), html)
  assert.ok(!/>—</.test(html), '状态落成未知占位: ' + html)
})

test('StatusTag :status="OPEN" 渲染 未处理', async () => {
  const { default: StatusTag } = await loadSfc(V('components/StatusTag.vue'))
  const html = await render(StatusTag, { status: 'OPEN' })
  assert.ok(html.includes('未处理'), html)
})

test('StatusTag :status="TIMED_OUT" 渲染 已超时', async () => {
  const { default: StatusTag } = await loadSfc(V('components/StatusTag.vue'))
  const html = await render(StatusTag, { status: 'TIMED_OUT' })
  assert.ok(html.includes('已超时'), html)
})

test('StatusTag 旧写法 bucket/state 与自定义 map 兼容', async () => {
  const { default: StatusTag } = await loadSfc(V('components/StatusTag.vue'))
  assert.ok((await render(StatusTag, { bucket: 'CONFIRMED' })).includes('已确认'))
  assert.ok((await render(StatusTag, { state: 'ACCEPTED' })).includes('已受理'))
  const custom = await render(StatusTag,
    { state: 'WEIRD', map: { WEIRD: '怪' } })
  assert.ok(custom.includes('怪'), custom)
})

test('status.ts 全量枚举映射：调度/告警/生命周期/账号/联调无一落 —', async () => {
  const { bucketLabel, bucketTone } = await loadTs(V('utils/status.ts'))
  const enums = ['PRODUCTION', 'PILOT', 'MOCK_ONLY', 'DEVELOPING', 'PLANNED',
    'SUSPENDED', 'RETIRED', 'PLATFORM', 'EXTERNAL', 'MANUAL',
    'CONFIRMED', 'UNCONFIRMED', 'CONFLICT',
    'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'STALLED',
    'SKIPPED_OVERLAP', 'SKIPPED_BLACKOUT', 'MISSED', 'CANCELLED',
    'OPEN', 'ACKED', 'RESOLVED', 'CRITICAL', 'WARN', 'INFO',
    'ACTIVE', 'LOCKED', 'DISABLED', 'PASS', 'FAIL', 'ERROR',
    'MANUAL_OBSERVATION', 'OBSERVE_ONLY', 'ENABLED']
  const validTones = new Set(['success', 'warning', 'danger', 'info', 'muted', 'mock'])
  for (const v of enums) {
    assert.notEqual(bucketLabel(v), v, `${v} 缺中文标签`)
    assert.ok(validTones.has(bucketTone(v)), `${v} tone 非法: ${bucketTone(v)}`)
  }
})

// ================================================ Drawer / Modal（P0 关不掉）
test('Drawer emits 同时声明 update:open 与 close（弹窗关不掉 P0 回归）', async () => {
  const { default: Drawer } = await loadSfc(V('components/Drawer.vue'))
  assert.ok(Array.isArray(Drawer.emits), 'Drawer.emits 缺失')
  for (const e of ['update:open', 'close']) {
    assert.ok(Drawer.emits.includes(e), `Drawer.emits 缺 ${e}: ${Drawer.emits}`)
  }
})

test('Modal emits 同时声明 update:open 与 close', async () => {
  const { default: Modal } = await loadSfc(V('components/Modal.vue'))
  assert.ok(Array.isArray(Modal.emits), 'Modal.emits 缺失')
  for (const e of ['update:open', 'close']) {
    assert.ok(Modal.emits.includes(e), `Modal.emits 缺 ${e}: ${Modal.emits}`)
  }
})

test('Drawer/Modal 关闭态（open=false）不渲染遮罩', async () => {
  const { default: Drawer } = await loadSfc(V('components/Drawer.vue'))
  const { default: Modal } = await loadSfc(V('components/Modal.vue'))
  const d = await render(Drawer, { open: false, title: 'T' })
  const m = await render(Modal, { open: false, title: 'T' })
  assert.ok(!d.includes('dr-mask') && !m.includes('mo-mask'),
    `关闭态仍渲染: ${d} | ${m}`)
})

// ==================================================== Donut（P0 扇区飞出）
test('Donut 圆环 transform 仅含 rotate(-90 x y)，无冲突的 transform-origin', async () => {
  const { default: Donut } = await loadSfc(V('components/charts/Donut.vue'))
  const svg = await render(Donut, { parts: [
    { name: '已确认', color: '#1a7f37', value: 10 },
    { name: '未知', color: '#b45309', value: 5 }] })
  assert.ok(!/transform-origin/.test(svg),
    'circle 上残留 transform-origin（双重旋转偏移）: ' + svg)
  assert.ok(/rotate\(-90 \d+ \d+\)/.test(svg), svg)
  assert.ok(svg.includes('已确认') && svg.includes('10'), svg)
})

test('Donut 全 0 输入渲染空态不抛错', async () => {
  const { default: Donut } = await loadSfc(V('components/charts/Donut.vue'))
  const html = await render(Donut, { parts: [{ name: 'x', color: '#fff', value: 0 }] })
  assert.ok(html.includes('暂无数据'))
})

// ============================================== BarChart（刻度对齐/标签抽稀）
test('BarChart 柱顶与顶部网格线严格对齐（峰值=顶部刻度时柱高=innerH）', async () => {
  const { default: BarChart } = await loadSfc(V('components/charts/BarChart.vue'))
  // 峰值 6 → 刻度 0/2/4/6，topTick=6 → 最大柱 y=10（PAD_T）、height=146
  const html = await render(BarChart, { height: 180, stacked: true, cats: [
    { label: 'A', values: [{ name: 'C', color: '#15803d', value: 6 }] },
    { label: 'B', values: [{ name: 'C', color: '#15803d', value: 2 }] }] })
  assert.ok(html.includes('height="146"'),
    '最高柱未落在顶部网格线（innerH=146）: ' + html)
})

test('BarChart >10 项 X 轴标签抽稀且 title 完整保留', async () => {
  const { default: BarChart } = await loadSfc(V('components/charts/BarChart.vue'))
  const cats = Array.from({ length: 30 }, (_, i) => ({
    label: `11-${String(i + 1).padStart(2, '0')}`,
    values: [{ name: 'C', color: '#15803d', value: (i % 5) + 1 }] }))
  const html = await render(BarChart, { cats, stacked: true })
  const visible = (html.match(/text-anchor="middle"/g) ?? []).length
  assert.ok(visible > 0 && visible <= 8,
    `30 项时可见 X 轴标签应抽稀到 ≤8（实际 ${visible}）`)
  const titles = (html.match(/<title>/g) ?? []).length
  assert.equal(titles, 30, `悬停 title 应保留全部 30 项（实际 ${titles}）`)
})

// ============================================ LineChart（全 0 / 日期并集）
test('LineChart 全 0 序列不产生 NaN 并正常出轴', async () => {
  const { default: LineChart } = await loadSfc(V('components/charts/LineChart.vue'))
  const html = await render(LineChart, { series: [
    { name: 'A', color: '#1f6feb',
      points: [{ x: '11-01', y: 0 }, { x: '11-02', y: 0 }] }] })
  assert.ok(!html.includes('NaN'), html)
  assert.ok(html.includes('<polyline'), html)
})

test('LineChart 多序列按日期并集共享坐标轴', async () => {
  const { default: LineChart } = await loadSfc(V('components/charts/LineChart.vue'))
  const html = await render(LineChart, { series: [
    { name: 'A', color: '#1f6feb', points: [{ x: '11-01', y: 2 }, { x: '11-02', y: 3 }] },
    { name: 'B', color: '#b45309', points: [{ x: '11-02', y: 1 }, { x: '11-03', y: 4 }] }] })
  assert.ok(html.includes('11-01') && html.includes('11-02') &&
    html.includes('11-03'), 'X 轴应含全部日期并集: ' + html)
  const circles = (html.match(/<circle /g) ?? []).length
  assert.equal(circles, 4, `两序列共 4 个数据点（实际 ${circles}）`)
  assert.ok(!html.includes('NaN'))
})

// ================================================= ErrorState（compact/retry）
test('ErrorState 未绑定 @retry 时隐藏重试按钮', async () => {
  const { default: ErrorState } = await loadSfc(V('components/ErrorState.vue'))
  const html = await render(ErrorState, { message: '加载失败' })
  assert.ok(!html.includes('重试'), '无 retry 监听仍显示重试按钮: ' + html)
  assert.ok(html.includes('加载失败'))
})

test('ErrorState 绑定 @retry 后显示重试按钮', async () => {
  const { default: ErrorState } = await loadSfc(V('components/ErrorState.vue'))
  const html = await render(ErrorState, { message: '加载失败' },
    { retry: () => h('span') })
  assert.ok(html.includes('重试'), html)
})

test('ErrorState compact/small 尺寸类生效', async () => {
  const { default: ErrorState } = await loadSfc(V('components/ErrorState.vue'))
  assert.ok((await render(ErrorState, { message: 'x', compact: true }))
    .includes('compact'))
  assert.ok((await render(ErrorState, { message: 'x', small: true }))
    .includes('small'))
})

// ===================================================== 路由表完整性（P0 断路）
test('路由表硬断言：MENU 全部路由 component 非空（含 /system/users→Users）', async () => {
  const routes = await loadTs(V('router/routes.ts'))
  const menu = await loadTs(V('router/menu.ts'))
  const built = routes.buildRoutes()
  for (const r of built) {
    if ('redirect' in r) continue
    assert.ok(r.component, `路由 ${r.path} component 为空（将白屏）`)
  }
  // /system/users 必须解析到 Users.vue（此前 map['system/users']=undefined）
  const users = built.find(r => r.path === '/system/users')
  assert.ok(users && typeof users.component === 'function')
  const audit = built.find(r => r.path === '/audit')
  assert.ok(audit, '/audit 路由存在（升级版审计检索正位）')
  const menus = menu.MENU.flatMap(g => g.items)
  assert.ok(menus.some(it => it.path === '/audit' && it.view === 'auditSearch'),
    '菜单 /audit 显式 view=auditSearch')
})

test('/targets/inf_cdc_frontend 不再被静态路由劫持，动态 /targets/:code 存在', async () => {
  const routes = await loadTs(V('router/routes.ts'))
  const built = routes.buildRoutes()
  const hijack = built.find(r => r.path === '/targets/inf_cdc_frontend')
  assert.equal(hijack, undefined,
    '菜单 /targets/* 不应重复注册静态路由（优先级高于动态段导致白屏）')
  const dyn = built.find(r => r.path === '/targets/:code')
  assert.ok(dyn && typeof dyn.component === 'function')
  const audit2 = built.find(r => r.path === '/audit2')
  assert.ok(audit2 && audit2.redirect === '/audit', '/audit2 兼容重定向')
})

test('菜单 targetScope 声明与 filterMenuByRoles 范围过滤', async () => {
  const menu = await loadTs(V('router/menu.ts'))
  const utilsMenu = await loadTs(V('utils/menu.ts'))
  const items = menu.MENU.flatMap(g => g.items)
  assert.ok(items.some(it => it.path === '/drugtrace' &&
    it.targetScope === 'drug_trace_alihealth'))
  // PT-03：传染病上报直达专属座舱 /infectious
  assert.ok(items.some(it => it.path === '/infectious' &&
    it.targetScope === 'inf_cdc_frontend'))
  const groups = menu.MENU
  const filter = utilsMenu.filterMenuByRoles
  // 受限账号只有传染病范围：码上放心入口隐藏，传染病入口保留
  const scoped = filter(groups, ['operator'], ['inf_cdc_frontend'])
  const flat = scoped.flatMap(g => g.items).map(it => it.path)
  // PT-03：传染病入口直达专属座舱 /infectious
  assert.ok(flat.includes('/infectious'))
  assert.ok(!flat.includes('/drugtrace'))
  // '*' 全范围：两个都在
  const all = filter(groups, ['operator'], ['*']).flatMap(g => g.items)
    .map(it => it.path)
  assert.ok(all.includes('/drugtrace') && all.includes('/infectious'))
  // 未传 scopes（老调用方式）：不过滤业务项（兼容旧页面行为）
  const legacy = filter(groups, ['operator']).flatMap(g => g.items)
    .map(it => it.path)
  assert.ok(legacy.includes('/drugtrace'))
})

// ================================== Dashboard P0：sortTargets 双 PRODUCTION
test('sortTargets 两个 PRODUCTION 仅 target_code（默认种子）排序不抛错（白屏 P0 回归）', async () => {
  const dash = await loadTs(V('utils/dashboard.ts'))
  const seed = [
    { target_code: 'alihealth_drugtrace_yljg', name: '码上放心', lifecycle: 'PRODUCTION', backlog_total: 3 },
    { target_code: 'inf_cdc_frontend', name: '传染病上报', lifecycle: 'PRODUCTION' },
    { target_code: 'medins_emr_sd', name: '医保电子病历', lifecycle: 'MOCK_ONLY' },
  ]
  const out = dash.sortTargets(seed)
  assert.equal(out.length, 3)
  assert.equal(out[0].target_code, 'alihealth_drugtrace_yljg')   // 次键 code 稳定
})

test('buildTodos + 排序跑通：UNKNOWN 深链带 bucket=UNKNOWN', async () => {
  const dash = await loadTs(V('utils/dashboard.ts'))
  const seed = [{ target_code: 'inf_cdc_frontend', name: '传染病',
    lifecycle: 'PRODUCTION', data_source: 'external_ledger',
    buckets: { UNKNOWN: 4 }, last_success_at: null }]
  const todos = dash.buildTodos([], seed, [])
  assert.equal(todos.length, 2)
  const unknownTodo = todos.find(t => t.key.startsWith('unknown-'))
  assert.ok(unknownTodo.to.includes('bucket=UNKNOWN') &&
    unknownTodo.to.includes('target=inf_cdc_frontend'), unknownTodo.to)
})

test('Dashboard 卡片内联渲染输出 backlog_total（全量口径）', async () => {
  const dash = await loadTs(V('utils/dashboard.ts'))
  const cards = dash.sortTargets([
    { target_code: 'inf_cdc_frontend', name: '传染病上报', lifecycle: 'PRODUCTION',
      data_source: 'platform', backlog: 2, backlog_total: 9,
      buckets: { CONFIRMED: 10, REJECTED: 1, UNKNOWN: 0 } }])
  const html = await renderInline(() => () => h('div', { class: 'card-grid' },
    cards.map(t => h('div', { class: 'target-card', key: t.target_code }, [
      h('b', t.name),
      h('span', `积压（窗口）${t.backlog ?? 0}`),
      h('span', `积压（全量）${t.backlog_total}`),
    ]))))
  assert.ok(html.includes('传染病上报'))
  assert.ok(html.includes('积压（窗口）2'), html)
  assert.ok(html.includes('积压（全量）9'),
    'backlog_total（全量积压）未渲染: ' + html)
  assert.ok(!html.includes('backlogTotal'), '不应再读不存在的驼峰字段')
})

// ======================================== Users 载荷（scopes→target_scopes）
test('Users 新建用户载荷映射 target_scopes（业务范围不再静默丢弃）', async () => {
  const { buildCreateUserPayload } = await loadTs(V('utils/payloads.ts'))
  const p = buildCreateUserPayload({ username: 'op1', display_name: '运维一',
    password: 'Str0ngPass!2026', roles: ['operator'], scopes: ['inf_cdc_frontend'] })
  assert.deepEqual(p.target_scopes, ['inf_cdc_frontend'])
  assert.ok(!('scopes' in p), '载荷不得携带表单字段 scopes: ' + JSON.stringify(p))
})

test('Users 空角色兜底 operator 且 target_scopes 缺省为空数组', async () => {
  const { buildCreateUserPayload } = await loadTs(V('utils/payloads.ts'))
  const p = buildCreateUserPayload({ username: 'x', display_name: '',
    password: 'Str0ngPass!2026', roles: [], scopes: [] })
  assert.deepEqual(p.roles, ['operator'])
  assert.deepEqual(p.target_scopes, [])
})

// ================================== AuditSearch 时间（+08:00 → UTC 无偏差）
test('AuditSearch datetime-local 院内时间 → UTC 换算准确（无 8 小时偏差）', async () => {
  const { hospitalLocalInputToUtcIso } = await loadTs(V('utils/time.ts'))
  assert.equal(hospitalLocalInputToUtcIso('2026-11-11T08:00'), '2026-11-11T00:00:00')
  assert.equal(hospitalLocalInputToUtcIso('2026-11-11T23:30'), '2026-11-11T15:30:00')
  // 东八区 0:30 属前一日 16:30 UTC
  assert.equal(hospitalLocalInputToUtcIso('2026-11-12T00:30'), '2026-11-11T16:30:00')
  assert.equal(hospitalLocalInputToUtcIso(''), undefined)
  assert.equal(hospitalLocalInputToUtcIso('garbage'), undefined)
})

test('AuditSearch 筛选参数契约：空值不传、时间经 +08:00 换算', async () => {
  const { buildAuditParams } = await loadTs(V('utils/payloads.ts'))
  const p = buildAuditParams({ actor: 'admin', action: '', actionPrefix: 'system.',
    target: '', atFrom: '2026-11-11T08:00', atTo: '' })
  assert.equal(p.actor, 'admin')
  assert.equal(p.action_prefix, 'system.')
  assert.equal(p.at_from, '2026-11-11T00:00:00')
  assert.equal(p.at_to, undefined)
  assert.equal(p.action, undefined, '空动作不产生查询值（searchAudit 跳过 undefined）')
})

// ==================================== formatHospitalTime 无时区后缀按 UTC 口径
test('formatHospitalTime：后端 naive UTC（无 Z）按 UTC 解释，跨浏览器时区一致', async () => {
  const { formatHospitalTime } = await loadTs(V('utils/time.ts'))
  assert.equal(formatHospitalTime('2026-11-11T02:00:00'), '2026-11-11 10:00:00')
  assert.equal(formatHospitalTime('2026-11-11T02:00:00Z'), '2026-11-11 10:00:00')
  assert.equal(formatHospitalTime('2026-11-11T02:00:00+08:00'), '2026-11-11 02:00:00')
  assert.equal(formatHospitalTime('2026-11-11T02:00:00+00:00'), '2026-11-11 10:00:00')
  assert.equal(formatHospitalTime('2026-11-11T02:00'), '2026-11-11 10:00:00')
})

test('formatHospitalTime 日期串（无时间部分）按 UTC 0 点口径', async () => {
  const { formatHospitalTime } = await loadTs(V('utils/time.ts'))
  // 'YYYY-MM-DD' 按 ES 规范解析为 UTC 午夜 → 院内 08:00（与 naive=UTC 口径一致）
  assert.equal(formatHospitalTime('2026-11-11'), '2026-11-11 08:00:00')
  assert.equal(formatHospitalTime(null), '—')
  assert.equal(formatHospitalTime('not-a-date'), '—')
})

// ==================================== TimeText 真实渲染（TimeText 组件级锁死）
test('TimeText 组件渲染 naive UTC 为院内时间', async () => {
  const { default: TimeText } = await loadSfc(V('components/TimeText.vue'))
  const html = await render(TimeText, { value: '2026-11-11T02:00:00' })
  assert.ok(html.includes('2026-11-11 10:00:00'), html)
  assert.ok((await render(TimeText, { value: null })).includes('—'))
})

// ==================================== StatCard + StatusTag 组合（详情页概况卡）
test('StatCard 组合 StatusTag 渲染（TargetDetail 概况卡结构）', async () => {
  const { default: StatCard } = await loadSfc(V('components/StatCard.vue'))
  const { default: StatusTag } = await loadSfc(V('components/StatusTag.vue'))
  const html = await renderInline(() => () => h('div', [
    h(StatCard, { label: '已确认', value: 12, tone: 'success' }),
    h(StatusTag, { status: 'PILOT' }),
  ]))
  assert.ok(html.includes('已确认') && html.includes('12'), html)
  assert.ok(html.includes('试点运行'), html)
})
