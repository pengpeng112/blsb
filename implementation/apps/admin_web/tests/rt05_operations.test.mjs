// RT-05 U10：运营写闭环测试（SFC_CACHE_NS=rt05v1 专属缓存命名空间）。
// 覆盖：映射无损互转（8算子/扩展键/非法输入）、JSON Diff（含键序不产伪差异）、
// DAG 布局（链/分支汇聚/孤立/环/悬空/重复ID）、CSV RFC4180 解析、
// 14 个本轮页面真实 SFC SSR（关键按钮/表格/空错态/只读角色）、
// DiffViewer/DagGraph 组件 SSR、写操作静态契约（零POST/409保留/禁重复/理由）。
process.env.SFC_CACHE_NS = 'rt05v1'          // 必须在 import sfc_loader 之前
import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import fs from 'node:fs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.dirname(HERE)
const V = (p) => path.join(ROOT, 'src', p)

import { parseRulesJson, rulesToRows, rowsToRules, validateRows,
  normalizeDictRefs, RULE_OPS } from '../src/utils/mappingConvert.mjs'
import { diffJson, summarizeDiff } from '../src/utils/jsonDiff.mjs'
import { layoutDag, stateFill } from '../src/utils/dagLayout.mjs'
import { parseCsvText } from '../src/utils/csvParse.mjs'

const VIEWS = [
  'Targets.vue', 'TargetDetail.vue', 'Schedules.vue', 'Alerts.vue',
  'Cases.vue', 'Quality.vue', 'Review.vue', 'Connectors.vue', 'Mapping.vue',
  'Lineage.vue', 'Workflows.vue', 'Pack.vue', 'Publications.vue', 'Metrics.vue',
]

// ================================================================ U05 映射互转
test('mapping: 默认模板可解析且 8 算子白名单与后端一致', () => {
  const p = parseRulesJson(JSON.stringify({ ops: [
    { target: 'org', op: 'field', source_field: 'org' }] }))
  assert.ok(p.ok && !p.advancedOnly)
  const ops = RULE_OPS.map(o => o.op)
  assert.deepEqual(ops, ['const', 'field', 'dict_map', 'concat', 'date_format',
    'decimal_scale', 'regex_replace', 'null_fallback'])
  assert.ok(!ops.includes('coalesce'))          // 旧计划 coalesce 不是当前算子
})

test('mapping: 非法 JSON / 非 ops 数组被拒绝并带原因', () => {
  assert.ok(!parseRulesJson('{bad').ok)
  const r = parseRulesJson('{"ops": "notarray"}')
  assert.ok(!r.ok && /ops/.test(r.error))
})

test('mapping: 未知算子 → advancedOnly（不进表格重组）', () => {
  const p = parseRulesJson(JSON.stringify({ ops: [
    { target: 'x', op: 'custom_secret_op', custom_key: 1 }] }))
  assert.ok(p.ok && p.advancedOnly)
})

test('mapping: 表格往返无损——未知扩展键/顺序/空值/类型保持', () => {
  const rules = { ops: [
    { target: 'a', op: 'field', source_field: 'sa', extra_unknown: { k: 1 } },
    { target: 'b', op: 'const', value: '' },                 // 空串保留
    { target: 'c', op: 'decimal_scale', source_field: 'sc', places: 0 },
    { target: 'd', op: 'concat', parts: ['x', 'y'] },
  ] }
  const { rows } = rulesToRows(rules)
  assert.equal(rows.length, 4)
  assert.deepEqual(rows[0]._extra, { extra_unknown: { k: 1 } })
  const back = rowsToRules(rows)
  assert.deepEqual(back, rules)                 // 逐字段/类型/顺序完全一致
})

test('mapping: 非法 JSON 提交前拦截（rowsToRules 前先 parse）', () => {
  const bad = rulesToRows({ nope: true })
  assert.equal(bad.rows, null)
})

test('mapping: 行校验——必填缺失/数字/危险正则', () => {
  const errs = validateRows([
    { _idx: 0, op: 'dict_map', target: 'k', params: { source_field: '' }, _extra: null },
    { _idx: 1, op: 'decimal_scale', target: 'n', params: { source_field: 's', places: 'abc' }, _extra: null },
    { _idx: 2, op: 'regex_replace', target: 'r', params: { source_field: 's', pattern: '.*.*.*', replacement: 'x' }, _extra: null },
  ])
  const msgs = errs.map(e => e.message).join('|')
  assert.ok(/必填/.test(msgs))
  assert.ok(/须为数字/.test(msgs))
  assert.ok(/灾难性回退/.test(msgs))
})

test('mapping: 字典引用禁止 latest 漂移', () => {
  const bad = normalizeDictRefs([{ namespace: 'enc', version: 'latest' }])
  assert.ok(!bad.ok && /latest/.test(bad.error))
  const ok = normalizeDictRefs([{ dictionary: 'enc', version: 'demo-v2' }])
  assert.ok(ok.ok && ok.refs[0].namespace === 'enc' &&
    ok.refs[0].version === 'demo-v2')
})

// ================================================================ U06 Diff
test('diff: 修改与类型变化', () => {
  const d = diffJson({ a: 1 }, { a: 2, b: 'x' })
  assert.deepEqual(d.map(e => `${e.path}:${e.kind}`).sort(),
    ['a:changed', 'b:added'])
  const t = diffJson({ a: '1' }, { a: 1 })
  assert.equal(t[0].kind, 'type_changed')
})

test('diff: 删除与嵌套路径', () => {
  const d = diffJson({ a: { b: { c: 1, d: 2 } } }, { a: { b: { c: 1 } } })
  assert.equal(d[0].kind, 'removed')
  assert.equal(d[0].path, 'a.b.d')
})

test('diff: 数组按索引（尾部新增/中间删除）', () => {
  const d = diffJson({ l: [1, 2, 3] }, { l: [1, 9, 3, 4] })
  const kinds = Object.fromEntries(d.map(e => [e.path, e.kind]))
  assert.equal(kinds['l[1]'], 'changed')
  assert.equal(kinds['l[3]'], 'added')
})

test('diff: 键顺序变化不制造伪差异', () => {
  const d = diffJson({ a: 1, b: 2 }, { b: 2, a: 1 })
  assert.deepEqual(d, [])
  assert.deepEqual(summarizeDiff(d).total, 0)
})

test('diff: 深相等为空集', () => {
  const o = { x: [1, { y: 'z' }], k: null }
  assert.deepEqual(diffJson(o, JSON.parse(JSON.stringify(o))), [])
})

test('diff: null 与字段缺失语义（added/removed 不产 type 噪声）', () => {
  const d = diffJson({ a: null }, { b: null })
  assert.deepEqual(d.map(e => e.kind), ['removed', 'added'])
})

// ================================================================ U07 DAG
test('dag: 链式布局按拓扑分层', () => {
  const g = layoutDag({ nodes: [
    { id: 'a', type: 'parse', label: '解析' },
    { id: 'b', type: 'validate', label: '校验' },
    { id: 'c', type: 'deliver.push', label: '推送' }],
    edges: [{ from: 'a', to: 'b', on: 'success' },
            { from: 'b', to: 'c', on: 'success' }] })
  assert.ok(!g.errors.length)
  assert.equal(g.layers, 3)
  const byId = Object.fromEntries(g.nodes.map(n => [n.id, n]))
  assert.ok(byId.a.y < byId.b.y && byId.b.y < byId.c.y)
})

test('dag: 分支汇聚与长标签截断', () => {
  const g = layoutDag({ nodes: [
    { id: 'a', type: 'parse', label: 'A' },
    { id: 'b', type: 'validate', label: 'B-非常长的标签-超出十四个字符会截断' },
    { id: 'c', type: 'notify', label: 'C' }],
    edges: [{ from: 'a', to: 'c' }, { from: 'b', to: 'c' }] })
  assert.ok(!g.errors.length)
  const c = g.nodes.find(n => n.id === 'c')
  assert.equal(c.layer, 1)                       // 汇聚点在最深层
  assert.ok(g.nodes.find(n => n.id === 'b').label.length <= 14)
})

test('dag: 孤立节点可见并标记', () => {
  const g = layoutDag({ nodes: [
    { id: 'a', type: 'parse', label: 'A' },
    { id: 'lonely', type: 'reconcile', label: '孤立' }],
    edges: [] })
  assert.ok(!g.errors.length)
  const n = g.nodes.find(x => x.id === 'lonely')
  assert.ok(n && n.isolated)
})

test('dag: 环明确报错且节点不入图（不死循环）', () => {
  const g = layoutDag({ nodes: [
    { id: 'a', type: 'parse', label: 'A' },
    { id: 'b', type: 'validate', label: 'B' }],
    edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'a' }] })
  assert.ok(g.errors.some(e => /环/.test(e)))
  assert.equal(g.nodes.length, 0)
})

test('dag: 悬空边与重复节点 id 明确报错', () => {
  const g = layoutDag({ nodes: [
    { id: 'a', type: 'parse', label: 'A' },
    { id: 'a', type: 'parse', label: 'A2' }],
    edges: [{ from: 'a', to: 'ghost', on: 'invalid' }] })
  assert.ok(g.errors.some(e => /duplicate node id a/.test(e)))
  assert.ok(g.errors.some(e => /dangling edge/.test(e)))
})

test('dag: 未知 Step 状态仍可见（不伪装成功）', () => {
  const unk = stateFill('WEIRD_NEW_STATE')
  const ok = stateFill('SUCCEEDED')
  assert.notEqual(unk.fill, ok.fill)
  assert.equal(stateFill(null).stroke, '#cbd5e1')   // 未开始：中性
})

// ================================================================ U09 CSV
test('csv: 引号字段内逗号不被拆列', () => {
  const r = parseCsvText('a,b\n"x,1",2')
  assert.deepEqual(r.rows[0], ['x,1', '2'])
})

test('csv: 引号字段内换行保持单行记录', () => {
  const r = parseCsvText('a,b\n"line1\nline2",y')
  assert.equal(r.rows.length, 1)
  assert.equal(r.rows[0][0], 'line1\nline2')
})

test('csv: 转义引号 "" → 字面引号', () => {
  const r = parseCsvText('a\n"he said ""hi"""')
  assert.equal(r.rows[0][0], 'he said "hi"')
})

test('csv: UTF-8 BOM 剥离 + CRLF', () => {
  const r = parseCsvText('\uFEFFa,b\r\n1,2\r\n')
  assert.deepEqual(r.headers, ['a', 'b'])
  assert.deepEqual(r.rows[0], ['1', '2'])
})

test('csv: 列数错报行号（原文行号含引号内换行偏移）', () => {
  const r = parseCsvText('a,b,c\n1,2\n"q\nq",3,4')
  assert.equal(r.errors.length, 1)
  assert.ok(/列数不匹配/.test(r.errors[0].message))
})

test('csv: 空行跳过并计数（不产错误）', () => {
  const r = parseCsvText('a,b\n\n1,2\n\n\n3,4')
  assert.equal(r.rows.length, 2)
  assert.equal(r.skippedEmptyLines, 3)
})

test('csv: 纯空输入零记录零错误', () => {
  const r = parseCsvText('\n\n')
  assert.equal(r.rows.length, 0)
  assert.equal(r.headers.length, 0)
})

// ================================================================ U10 14 页真实 SFC SSR
const { loadSfc } = await import('./sfc_loader.mjs')
const { createSSRApp, h } = await import('vue')
const { renderToString } = await import('vue/server-renderer')

async function renderView (rel, props = {}) {
  const { default: comp } = await loadSfc(V(path.join('views', rel)))
  const app = createSSRApp({ render: () => h(comp, props) })
  return renderToString(app)
}

test('SSR Targets：页头/表格骨架渲染；新建按钮只对 admin（SSR 无角色不出现）',
  async () => {
    const html = await renderView('Targets.vue')
    assert.ok(html.includes('上报业务'))
    assert.ok(html.includes('新建业务') === false || !html.includes('新建业务'))
    const src = fs.readFileSync(V('views/Targets.vue'), 'utf8')
    assert.ok(src.includes("v-if=\"isAdmin\""))       // 角色分支真实存在
    assert.ok(src.includes('createTarget') && src.includes('listPlugins'))
  })

test('SSR TargetDetail：无 code 时走加载/错误分支不崩', async () => {
  const html = await renderView('TargetDetail.vue')
  assert.ok(html.length > 100)
  const src = fs.readFileSync(V('views/TargetDetail.vue'), 'utf8')
  assert.ok(src.includes('lifecycle 升级到 PILOT/PRODUCTION 必填理由') ||
    src.includes('理由 *（升级到 PILOT/PRODUCTION 必填'))
  assert.ok(src.includes('editConflict'))          // 409 保留输入分支
})

test('SSR Schedules：新建（默认停用）与编辑契约标记在位', async () => {
  const html = await renderView('Schedules.vue')
  assert.ok(html.includes('调度任务'))
  const src = fs.readFileSync(V('views/Schedules.vue'), 'utf8')
  assert.ok(src.includes('新建调度（默认停用）'))
  assert.ok(src.includes('cronSeq'))               // 请求序号防旧响应覆盖
  assert.ok(src.includes('schedulesMeta'))
})

test('SSR Alerts：规则新建 createAlertRule + 通道 env 正则校验', async () => {
  const html = await renderView('Alerts.vue')
  assert.ok(html.includes('告警'))
  const src = fs.readFileSync(V('views/Alerts.vue'), 'utf8')
  assert.ok(src.includes('createAlertRule') && src.includes('createChannel'))
  assert.ok(src.includes('^[A-Z][A-Z0-9_]{2,63}$'))
  assert.ok(src.includes('本批不可启用/测试外发'))
})

test('SSR Cases：状态筛选与详情失败保留主表分支', async () => {
  const html = await renderView('Cases.vue')
  assert.ok(html.includes('病理工作台'))
  const src = fs.readFileSync(V('views/Cases.vue'), 'utf8')
  assert.ok(src.includes('主表数据保留'))
  assert.ok(src.includes('barcode_raw'))           // 前导零原值列在位
})

test('SSR Quality：规则码中文说明 + 批量部分失败真实展示', async () => {
  const html = await renderView('Quality.vue')
  assert.ok(html.includes('质量队列'))
  const src = fs.readFileSync(V('views/Quality.vue'), 'utf8')
  assert.ok(src.includes('部分失败'))
  assert.ok(src.includes('（未登记说明）'))          // 未知规则不乱翻译
})

test('SSR Review：列表点选 + If-Match 乐观锁 + 理由必填', async () => {
  const html = await renderView('Review.vue')
  assert.ok(html.includes('关联复核'))
  const src = fs.readFileSync(V('views/Review.vue'), 'utf8')
  assert.ok(src.includes('If-Match'))
  assert.ok(src.includes('不强迫手填 UUID'))
})

test('SSR Connectors：结构化表单 + env 引用语义 + 高级 JSON 保留', async () => {
  const html = await renderView('Connectors.vue')
  assert.ok(html.includes('连接器配置向导'))
  const src = fs.readFileSync(V('views/Connectors.vue'), 'utf8')
  assert.ok(src.includes('高级配置 JSON') && src.includes('Object.assign(cfg, v)'))
  assert.ok(src.includes('env:VAR'))
})

test('SSR Mapping：表格/高级双模式 + advancedOnly 保护 + 字典显式版本', async () => {
  const html = await renderView('Mapping.vue')
  assert.ok(html.includes('字段映射'))
  const src = fs.readFileSync(V('views/Mapping.vue'), 'utf8')
  assert.ok(src.includes('不会重组丢配置'))
  assert.ok(src.includes('禁止"最新"漂移引用'))
})

test('SSR Lineage：三栏对照与"未提供"标记', async () => {
  const html = await renderView('Lineage.vue')
  assert.ok(html.includes('字段血缘') && html.includes('未提供'))
  const src = fs.readFileSync(V('views/Lineage.vue'), 'utf8')
  assert.ok(src.includes('不以设计文档冒充实测'))
})

test('SSR Workflows：零 POST 防呆与 DagGraph 接入', async () => {
  const html = await renderView('Workflows.vue')
  assert.ok(html.includes('流程编排'))
  const src = fs.readFileSync(V('views/Workflows.vue'), 'utf8')
  assert.ok(src.includes('零 POST'))
  assert.ok(src.includes('<DagGraph'))
})

test('SSR Pack：撤销/恢复语义分立 + DiffViewer 接入', async () => {
  const html = await renderView('Pack.vue')
  assert.ok(html.includes('医院配置包'))
  const src = fs.readFileSync(V('views/Pack.vue'), 'utf8')
  assert.ok(src.includes('rollbackRelease') && src.includes('restoreRelease'))
  assert.ok(src.includes('<DiffViewer'))
  // 修复的重复表达式不再出现在模板中（注释说明除外）
  assert.ok(!/<template>[\s\S]*p\.content\.pack_key \|\| p\.content\.pack_key/.test(src))
})

test('SSR Publications：撤回按钮仅 PUBLISHED 行 + 理由必填', async () => {
  const html = await renderView('Publications.vue')
  assert.ok(html.includes('视图发布'))
  const src = fs.readFileSync(V('views/Publications.vue'), 'utf8')
  assert.ok(src.includes("p.state === 'PUBLISHED'"))
  assert.ok(src.includes('require-reason') || src.includes('requireReason'))
})

test('SSR Metrics：本地解析 + 导入前确认 + Mock 标识', async () => {
  const html = await renderView('Metrics.vue')
  assert.ok(html.includes('运营指标导入'))
  const src = fs.readFileSync(V('views/Metrics.vue'), 'utf8')
  assert.ok(src.includes('parseCsvText'))
  assert.ok(src.includes('importConfirmed'))
  assert.ok(src.includes('Mock 上报'))
})

// ================================================================ DiffViewer / DagGraph 组件 SSR
test('SSR DiffViewer：新增/删除/类型变化渲染与摘要', async () => {
  const { default: comp } = await loadSfc(V('components/DiffViewer.vue'))
  const { createSSRApp, h } = await import('vue')
  const { renderToString } = await import('vue/server-renderer')
  const app = createSSRApp({ render: () => h(comp, {
    oldObj: { a: 1, gone: true, n: 'x' },
    newObj: { a: 2, n: 3 },
    oldLabel: '当前闭包', newLabel: '恢复目标' }) })
  const html = await renderToString(app)
  assert.ok(html.includes('当前闭包') && html.includes('恢复目标'))
  assert.ok(html.includes('新增') && html.includes('删除'))
  assert.ok(html.includes('类型变化'))
  assert.ok(html.includes('&gt;a') || (html.includes('a') &&
    html.includes('gone') && html.includes('n')))
})

test('SSR DagGraph：链式渲染节点/边；环结构错误可见', async () => {
  const { default: comp } = await loadSfc(V('components/DagGraph.vue'))
  const { createSSRApp, h } = await import('vue')
  const { renderToString } = await import('vue/server-renderer')
  const chain = { nodes: [
    { id: 'a', type: 'parse', label: '解析' },
    { id: 'b', type: 'validate', label: '校验' }],
    edges: [{ from: 'a', to: 'b', on: 'success' }] }
  const ok = await renderToString(createSSRApp({
    render: () => h(comp, { dsl: chain }) }))
  assert.ok(ok.includes('解析') && ok.includes('校验'))
  assert.ok(ok.includes('svg'))
  const cyc = await renderToString(createSSRApp({
    render: () => h(comp, { dsl: { nodes: [
      { id: 'a', type: 'parse', label: 'A' }, { id: 'b', type: 'parse', label: 'B' }],
      edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'a' }] } }) }))
  assert.ok(/环|错误/.test(cyc))
})

// ================================================================ 写操作静态契约
test('写契约：409/失败保留输入（Targets/TargetDetail/Mapping/Schedules）', () => {
  for (const [f, marks] of [
    ['Targets.vue', ['输入已保留', 'CONFLICT', 'confirm(']],
    ['TargetDetail.vue', ['你的输入已保留', 'revision']],
    ['Mapping.vue', ['输入已保留', '上次输入已保留']],
    ['Schedules.vue', ['你的输入已保留', 'revision']],
    ['Review.vue', ['理由已保留']],
    ['Quality.vue', ['部分失败']],
  ]) {
    const src = fs.readFileSync(V('views/' + f), 'utf8')
    for (const m of marks) assert.ok(src.includes(m), `${f} 缺标记 ${m}`)
  }
})

test('写契约：禁重复提交（busy disabled）与 PageHeader actions 入口', () => {
  for (const f of ['Targets.vue', 'Schedules.vue', 'Alerts.vue', 'Mapping.vue',
    'Pack.vue', 'Metrics.vue']) {
    const src = fs.readFileSync(V('views/' + f), 'utf8')
    assert.ok(/:disabled="[^"]*[Bb]usy[^"]*"/.test(src), `${f} 缺 busy 禁用`)
  }
})

test('写契约：理由必填（ConfirmDialog require-reason / textarea 理由）', () => {
  const pack = fs.readFileSync(V('views/Pack.vue'), 'utf8')
  const pub = fs.readFileSync(V('views/Publications.vue'), 'utf8')
  const sched = fs.readFileSync(V('views/Schedules.vue'), 'utf8')
  assert.ok(pack.includes('require-reason'))
  assert.ok(pub.includes('require-reason'))
  assert.ok(/理由.*≥5 字/.test(sched + pack))
})

test('写契约：写操作 method/path 真实指向（API 层）', () => {
  const ops = fs.readFileSync(V('api/ops.ts'), 'utf8')
  const rc = fs.readFileSync(V('api/reportCenter.ts'), 'utf8')
  assert.ok(ops.includes('/rollback'))
  assert.ok(ops.includes('/restore'))
  assert.ok(ops.includes('encodeURIComponent(id)'))
  assert.ok(ops.includes('/api/v1/schedules/${id}/enable'))
  assert.ok(rc.includes("core.post('/api/v1/report-targets'"))
  assert.ok(rc.includes('core.put('))
})
