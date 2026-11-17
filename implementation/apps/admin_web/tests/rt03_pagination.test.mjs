// RT-03 S09/S10：前端分页状态与渲染契约（node:test；零新依赖）。
// 覆盖：mergePagedItems 按 ID 去重（实时列表 last_seen_at 变化可致跨页
// 重复）；pageStateReduce reset/loaded/appended/failed（失败保留已加载）；
// durationLabel null 显示 —（不把未知耗时显示为 0）；runStateHint 的
// QUEUED/RUNNING/RETRY_WAIT 口径说明；三个改版视图真实编译渲染。
import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.dirname(HERE)
const V = (p) => path.join(ROOT, 'src', p)

import { loadSfc } from './sfc_loader.mjs'
import { h, createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'

import { mergePagedItems, pageStateReduce, durationLabel, runStateHint }
  from '../src/utils/paging.mjs'

// ================================================= mergePagedItems
test('mergePagedItems 按 id 去重且保留首次出现顺序', () => {
  const p1 = [{ id: 'a' }, { id: 'b' }, { id: 'c' }]
  const p2 = [{ id: 'c' }, { id: 'd' }, { id: 'e' }]   // 'c' 跨页重复
  const out = mergePagedItems(p1, p2)
  assert.deepEqual(out.map(r => r.id), ['a', 'b', 'c', 'd', 'e'])
})

test('mergePagedItems 不修改输入数组且容忍 null 新页', () => {
  const p1 = [{ id: 'a' }]
  const out = mergePagedItems(p1, null)
  assert.deepEqual(out, [{ id: 'a' }])
  assert.notEqual(out, p1)
  assert.equal(p1.length, 1)
})

// ================================================= pageStateReduce
test('pageStateReduce：loaded 重置列表、appended 合并、游标跟随响应', () => {
  const s0 = pageStateReduce({ items: [], nextCursor: null, loading: true,
    loadingMore: false, error: '' }, 'loaded',
    { items: [{ id: '1' }], next_cursor: 'c1' })
  assert.deepEqual(s0.items.map(r => r.id), ['1'])
  assert.equal(s0.nextCursor, 'c1')
  assert.equal(s0.loading, false)
  const s1 = pageStateReduce(s0, 'appended',
    { items: [{ id: '1' }, { id: '2' }], next_cursor: null })
  assert.deepEqual(s1.items.map(r => r.id), ['1', '2'])
  assert.equal(s1.nextCursor, null)                 // 末页
})

test('pageStateReduce：failed 保留已加载内容与游标（可重试）', () => {
  const s0 = { items: [{ id: '1' }], nextCursor: 'c1', loading: false,
    loadingMore: true, error: '' }
  const s1 = pageStateReduce(s0, 'failed', new Error('网络中断'))
  assert.deepEqual(s1.items, [{ id: '1' }])         // 失败不清列表
  assert.equal(s1.nextCursor, 'c1')                 // 游标保留
  assert.equal(s1.loadingMore, false)
  assert.ok(s1.error.includes('网络中断'))
})

test('pageStateReduce：reset 清空列表与游标（换筛选防旧游标串页）', () => {
  const s0 = { items: [{ id: '1' }], nextCursor: 'c1', loading: false,
    loadingMore: false, error: 'x' }
  const s1 = pageStateReduce(s0, 'reset')
  assert.deepEqual(s1.items, [])
  assert.equal(s1.nextCursor, null)
  assert.equal(s1.error, '')
})

// ================================================= 耗时/状态口径
test('durationLabel：null/undefined 显示 —（不冒充 0），0 显示 0s', () => {
  assert.equal(durationLabel(null), '—')
  assert.equal(durationLabel(undefined), '—')
  assert.equal(durationLabel(0), '0s')              // 不足1秒真实任务允许 0
  assert.equal(durationLabel(95), '95s')
})

test('runStateHint：QUEUED/RUNNING/RETRY_WAIT 有口径说明，其他为空', () => {
  assert.ok(runStateHint('QUEUED').includes('排队'))
  assert.ok(runStateHint('RUNNING').includes('首次开跑'))
  assert.ok(runStateHint('RETRY_WAIT').includes('重试'))
  assert.equal(runStateHint('SUCCEEDED'), '')
})

test('运行记录行渲染：null 耗时显示 —、QUEUED 行带排队说明（S09 契约）', async () => {
  const rows = [
    { id: '1', run_key: 'cron:a', state: 'QUEUED', duration_seconds: null },
    { id: '2', run_key: 'cron:b', state: 'SUCCEEDED', duration_seconds: 5 },
    { id: '3', run_key: 'cron:c', state: 'RUNNING', duration_seconds: null },
    { id: '4', run_key: 'cron:d', state: 'SUCCEEDED', duration_seconds: 0 },
  ]
  const render = () => h('table', {}, [
    h('tbody', {}, rows.map(r => h('tr', { key: r.id, title: runStateHint(r.state) }, [
      h('td', {}, runStateHint(r.state) || r.state),
      h('td', {}, durationLabel(r.duration_seconds)),
    ])))
  ])
  const html = await renderToString(createSSRApp({ render }))
  assert.ok(html.includes('—'), '未知耗时应显示 —: ' + html)
  assert.ok(html.includes('0s'))
  assert.ok(html.includes('排队'), html)
  assert.ok(html.includes('首次开跑'))
})

// ================================================= 改版视图模板编译
// 视图 script 依赖 import.meta.env（http.ts），node 直载不可执行——
// 用 @vue/compiler-sfc 做真实模板编译检查（parse+compileTemplate 全绿），
// 并断言分页/加载更多模板标记在位。
import fs from 'node:fs'
import { parse, compileTemplate } from '@vue/compiler-sfc'

function compileView(rel) {
  const file = V(rel)
  const src = fs.readFileSync(file, 'utf8')
  const { descriptor, errors } = parse(src, { filename: file })
  assert.deepEqual(errors, [], `${rel} parse 失败`)
  const tpl = compileTemplate({
    source: descriptor.template.content, filename: file, id: 'x' })
  assert.deepEqual(tpl.errors, [], `${rel} template 编译失败`)
  return src
}

test('Schedules.vue 模板编译通过且运行记录加载更多/口径说明在位', () => {
  const src = compileView('views/Schedules.vue')
  assert.ok(src.includes('loadMoreRuns'), '缺加载更多处理')
  assert.ok(src.includes('runsHasMore'), '缺末页判断')
  assert.ok(src.includes('runStateHint'), '缺状态口径说明')
  assert.ok(src.includes('durationLabel'), '缺耗时归一展示')
})

test('Alerts.vue 模板编译通过且告警/通知日志加载更多在位', () => {
  const src = compileView('views/Alerts.vue')
  assert.ok(src.includes('loadMoreAlerts'), '缺告警加载更多')
  assert.ok(src.includes('loadMoreLogs'), '缺日志加载更多')
  assert.ok(src.includes('aSeq'), '缺防旧响应覆盖序号')
})

test('MedinsDebug/Dashboard/TargetDetail 模板编译通过（S09 调用方同步）', () => {
  const medins = compileView('views/target/MedinsDebug.vue')
  assert.ok(medins.includes('loadMoreHistory'), '缺历史加载更多')
  compileView('views/Dashboard.vue')
  compileView('views/TargetDetail.vue')
})
