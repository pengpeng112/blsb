// PT-03：传染病全维监控专页前端测试（SFC_CACHE_NS=pt03v1）。
// 覆盖：InfectiousMonitor 五页签真实 SSR（页头/指标卡/13 表矩阵/守护 C1–C11/
// 晨报/错误码中文释义/回执明细筛选/对账）、路由与菜单接线、13 类文书中文名、
// 零工程黑话、dashboard ACCEPTED 口径纯函数、LedgerPane 外部记录抽屉结构化面板。
process.env.SFC_CACHE_NS = 'pt03v1'          // 必须在 import sfc_loader 之前
import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import fs from 'node:fs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.dirname(HERE)
const V = (p) => path.join(ROOT, 'src', p)

import { primarySuccessMetric, stackDaily, buildTodos }
  from '../src/utils/dashboard.mjs'
import { loadSfc } from './sfc_loader.mjs'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'

async function renderView (rel, props = {}) {
  const { default: comp } = await loadSfc(V(path.join('views', rel)))
  const app = createSSRApp({ render: () => h(comp, props) })
  return renderToString(app)
}

const src = (rel) => fs.readFileSync(V(rel), 'utf-8')

// ================================================================ 路由/菜单接线
test('pt03 route: /infectious 已注册且侧栏传染病上报直达', () => {
  const menu = src('router/menu.ts')
  assert.ok(menu.includes("path: '/infectious'"), 'menu 需有 /infectious 项')
  assert.ok(menu.includes("view: 'infectious'"))
  const routes = src('router/routes.ts')
  assert.ok(routes.includes("infectious: () => import('../views/InfectiousMonitor.vue')"))
})

test('pt03 route: /targets/inf_cdc_frontend 详情页有专页跳转横幅', () => {
  const td = src('views/TargetDetail.vue')
  assert.ok(td.includes("/infectious"))
  assert.ok(td.includes('前往传染病上报专页'))
})

// ================================================================ dashboard ACCEPTED 口径
test('pt03 dashboard: 主指标口径（已受理自适应+角标）', () => {
  const acceptedOnly = { buckets: { CONFIRMED: 0, ACCEPTED: 980 } }
  const m1 = primarySuccessMetric(acceptedOnly)
  assert.equal(m1.value, 980)
  assert.equal(m1.label, '已受理')
  assert.equal(m1.badge, '前置机受理')
  const confirmed = { buckets: { CONFIRMED: 5, ACCEPTED: 3 } }
  const m2 = primarySuccessMetric(confirmed)
  assert.equal(m2.value, 5)
  assert.equal(m2.label, '已确认')
})

test('pt03 dashboard: buildTodos 不再对受理型业务刷假警告', () => {
  const summaries = [{ target_code: 'inf_cdc_frontend',
    buckets: { CONFIRMED: 0, ACCEPTED: 500, UNKNOWN: 0 },
    last_success_at: null, data_source: 'external_ledger' }]
  const todos = buildTodos([], summaries, [])
  assert.ok(!todos.some(t => t.text.includes('尚无')), '不应再有"尚无业务确认"假警告')
})

test('pt03 dashboard: stackDaily 补绘 ACCEPTED 序列', () => {
  const rows = stackDaily([{ daily: [{ date: '2026-11-17', ACCEPTED: 5,
    CONFIRMED: 0, REJECTED: 1, UNKNOWN: 0 }] }])
  assert.equal(rows[0].ACCEPTED, 5)
})

// ================================================================ InfectiousMonitor SSR
test('pt03 SSR monitor: 页头五页签真实渲染（页签常显）', async () => {
  const html = await renderView('InfectiousMonitor.vue')
  assert.ok(html.includes('传染病上报'))
  assert.ok(html.includes('13 类临床文书实时监测'))
  for (const name of ['运行总览', '13 接口全景矩阵', '守护体检与企微晨报',
                      '提交回执明细', '对账与数据接入']) {
    assert.ok(html.includes(name), `缺页签：${name}`)
  }
  assert.ok(html.includes('同步最新状态') && html.includes('每 60 秒自动刷新'))
})

test('pt03 monitor: 五张指标卡与受理口径铁律（模板源断言）', () => {
  const s = src('views/InfectiousMonitor.vue')
  for (const kw of ['前置机已受理', '等待父表受理', '接口退回（02/03/04）',
                    '必填预检尾差', '网络/服务待重试']) {
    assert.ok(s.includes(kw), `缺指标卡：${kw}`)
  }
  assert.ok(s.includes('受理≠国家平台终审'), '受理口径铁律须明示')
  assert.ok(s.includes('主子门禁保护中'))
})

test('pt03 SSR monitor: 静态源含 13 类文书中文名与主子依赖', () => {
  const s = src('views/InfectiousMonitor.vue')
  for (const name of ['住院病案首页', '入院记录', '出院记录', '死亡记录',
                      '首次病程记录', '日常病程记录', '检查报告主表',
                      '检查报告项目', '检验报告主表', '检验报告项目',
                      '医嘱处方主表', '医嘱药品明细', '生命体征记录']) {
    assert.ok(s.includes(name), `缺文书名：${name}`)
  }
  assert.ok(s.includes('c11_relay_tunnel'), '守护体检须含 C11 隧道真达')
  assert.ok(s.includes('企微隧道真达'))
})

test('pt03 SSR monitor: api 客户端 5 端点齐全', () => {
  const api = src('api/infectious.ts')
  for (const ep of ['overview', 'tables', 'watchdog', 'reconcile', 'sync']) {
    assert.ok(api.includes(`/api/v1/infectious/monitor/${ep}`), ep)
  }
})

test('pt03 SSR monitor: 零工程黑话（专页与监控 API 层）', () => {
  for (const f of ['views/InfectiousMonitor.vue', 'api/infectious.ts']) {
    const s = src(f)
    for (const bad of ['NOT_ALLOWED_THIS_BATCH', 'revision 乐观锁',
                       'AUTO_MIXED(', 'source_tz=']) {
      if (f.endsWith('InfectiousMonitor.vue')) {
        assert.ok(!s.includes(bad), `${f} 出现黑话：${bad}`)
      }
    }
  }
  const im = src('views/InfectiousMonitor.vue')
  assert.ok(!im.includes('**'), '页面不得裸露 Markdown 星号')
})

test('pt03 SSR monitor: LedgerPane 外部记录抽屉结构化面板（不再空白）', () => {
  const s = src('views/target/LedgerPane.vue')
  assert.ok(s.includes("detail?.kind === 'external_record'"),
    '须有 external_record 专属模板分支')
  for (const kw of ['文书：', '证据等级：', '异常诊断', '源业务时间',
                    '最近推送时间', '累计尝试（可见下界）']) {
    assert.ok(s.includes(kw), `缺外部记录详情字段：${kw}`)
  }
  assert.ok(s.includes('PARENT_WAIT'), '阶段中文诊断须覆盖等待父受理')
  assert.ok(s.includes("'04'"), '错误码中文诊断须覆盖 04')
})

// ================================================================ ImportPane 混合口径默认
test('pt03 ImportPane: 默认推荐标准生产混合口径', () => {
  const s = src('views/target/ImportPane.vue')
  assert.ok(s.includes("ref<'AUTO_MIXED' | 'CST' | 'UTC'>('AUTO_MIXED')"),
    '导入默认选中 AUTO_MIXED（回执 UTC + 源数据 CST）')
  assert.ok(s.includes('标准生产混合口径（推荐'))
})
