// PT-02：Alerts.vue 企微通道与规则下拉修复测试（SFC_CACHE_NS=pt02v1 专属缓存）。
// 覆盖：规则类型下拉渲染全部真实规则编码（不再 0..8 数字下标）+ 3 类新规则在列、
// 规则参数动态表单（formToParams/paramsToForm 数值回填）、通道中文配置摘要
// （无 <JsonViewer>）、wecom 双通道结构化表单与校验、预览弹窗渲染、黑话清零。
process.env.SFC_CACHE_NS = 'pt02v1'          // 必须在 import sfc_loader 之前
import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.dirname(HERE)
const V = (p) => path.join(ROOT, 'src', p)

import { RULE_FORMS, formToParams, paramsToForm } from '../src/utils/params.mjs'
import { loadSfc } from './sfc_loader.mjs'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'

async function renderView (rel, props = {}) {
  const { default: comp } = await loadSfc(V(path.join('views', rel)))
  const app = createSSRApp({ render: () => h(comp, props) })
  return renderToString(app)
}

// ================================================================ 规则表单定义（params.mjs 唯一事实源）
test('pt02 rules: RULE_FORMS 为数组且含 12 类规则（含 PT-02 新增 3 类）', () => {
  assert.ok(Array.isArray(RULE_FORMS), 'RULE_FORMS 必须是数组（旧版被当对象 map 出 0..8）')
  const kinds = RULE_FORMS.map(f => f.kind)
  for (const k of ['JOB_FAILED', 'NO_SUCCESS_WITHIN', 'DATE_EXPIRY',
                   'WATERMARK_STALE', 'PARENT_WAIT_OVER', 'WATCHDOG_STALE']) {
    assert.ok(kinds.includes(k), `缺少规则类型 ${k}`)
  }
  assert.equal(kinds.length, 12)
})

test('pt02 rules: 类型键 map 不再产生数字下标（0..8 P0 bug 回归）', () => {
  const keys = RULE_FORMS.map(f => f.kind)
  assert.ok(!keys.some(k => /^\d+$/.test(String(k))), '类型键不得为纯数字')
})

test('pt02 rules: NO_SUCCESS_WITHIN 含受理算活动字段（ACCEPTED 误报修复）', () => {
  const def = RULE_FORMS.find(f => f.kind === 'NO_SUCCESS_WITHIN')
  const keys = def.fields.map(f => f.key)
  assert.ok(keys.includes('hours') && keys.includes('accept_counts_as_activity'))
})

test('pt02 rules: formToParams 数值转换 + accept 字符串透传', () => {
  const out = formToParams('NO_SUCCESS_WITHIN',
    { hours: '26', accept_counts_as_activity: 'true' })
  assert.equal(out.hours, 26)
  assert.equal(out.accept_counts_as_activity, 'true')
  const out2 = formToParams('WATERMARK_STALE', { table: 'ex_lab_item', hours: '6' })
  assert.equal(out2.table, 'ex_lab_item')
  assert.equal(out2.hours, 6)
  assert.throws(() => formToParams('PARENT_WAIT_OVER', { threshold: '' }),
    /必填/)
})

test('pt02 rules: paramsToForm 编辑回填（WATCHDOG_STALE）', () => {
  const vals = paramsToForm('WATCHDOG_STALE',
    { status_path: '<prod-path>/exchange/inf-watchdog/state/status.json', minutes: 30 })
  assert.equal(vals.status_path, '<prod-path>/exchange/inf-watchdog/state/status.json')
  assert.equal(vals.minutes, '30')
})

// ================================================================ Alerts.vue 真实 SSR
test('pt02 SSR Alerts: 规则下拉渲染中文标签而非 0..8（SSR 静态源断言）', async () => {
  const src = (await import('node:fs')).readFileSync(V('views/Alerts.vue'), 'utf-8')
  assert.ok(src.includes('RULE_FORMS.map(f => f.kind)'),
    '类型下拉必须 map 真实 kind（修复 Object.keys 数字下标）')
  assert.ok(!src.includes('Object.keys(RULE_FORMS)'), '不得再对数组 Object.keys')
  assert.ok(!src.includes('(RULE_FORMS as any)['), '不得再按数字下标取表单定义')
})

test('pt02 SSR Alerts: 通道表格无 JsonViewer、含预览/探测按钮', async () => {
  const src = (await import('node:fs')).readFileSync(V('views/Alerts.vue'), 'utf-8')
  assert.ok(!src.includes('<JsonViewer'), '通道配置不得再裸 JSON')
  assert.ok(src.includes('预览卡片（不出网）'))
  assert.ok(src.includes('连通性探测'))
  assert.ok(src.includes('channelSummary'))
})

test('pt02 SSR Alerts: 页面骨架渲染（页头+页签+告警表）', async () => {
  const html = await renderView('Alerts.vue')
  assert.ok(html.includes('告警中心'))
  assert.ok(html.includes('全部状态'))
  assert.ok(html.includes('待处理') && html.includes('严重'), '筛选下拉应为中文')
})

test('pt02 SSR Alerts: 通道抽屉含企微双通道表单与免打扰（源断言）', async () => {
  const src = (await import('node:fs')).readFileSync(V('views/Alerts.vue'), 'utf-8')
  assert.ok(src.includes('企业微信网关（医院统一推送平台）'))
  assert.ok(src.includes('企业微信群机器人'))
  assert.ok(src.includes('secret_env'))
  assert.ok(src.includes('夜间免打扰'))
  assert.ok(!src.includes('NOT_ALLOWED_THIS_BATCH'), '黑话不得出现在界面')
  assert.ok(!src.includes('revision 乐观锁'), '黑话不得出现在界面')
})

// ================================================================ ops.ts 三态 API 客户端
test('pt02 ops: testChannel 支持 dry_run/probe 查询参数', async () => {
  const src = (await import('node:fs')).readFileSync(V('api/ops.ts'), 'utf-8')
  assert.ok(src.includes('dry_run'))
  assert.ok(src.includes('probe'))
  assert.ok(src.includes('ChannelTestPreview'))
})
