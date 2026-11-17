// LT-06B 纯函数单测（node:test；直接 import .mjs 唯一事实源）。
import test from 'node:test'
import assert from 'node:assert/strict'
import { sortTargets, buildTodos, stackDaily, describeSchedule } from '../src/utils/dashboard.mjs'
import { RULE_FORMS, formToParams, paramsToForm } from '../src/utils/params.mjs'

// ---------------- sortTargets ----------------
test('sortTargets 按 lifecycle 排序且未知靠后、code 稳定', () => {
  const rows = [
    { code: 'b', lifecycle: 'PLANNED' },
    { code: 'a', lifecycle: 'PRODUCTION' },
    { code: 'z', lifecycle: 'MOCK_ONLY' },
    { code: 'c', lifecycle: 'PLANNED' },
    { code: 'x', lifecycle: 'WEIRD' },
  ]
  assert.deepEqual(sortTargets(rows).map(r => r.code),
    ['a', 'z', 'b', 'c', 'x'])
})

// ---------------- buildTodos ----------------
test('buildTodos：CRITICAL 优先、UNKNOWN 生成、限 5 条、去重', () => {
  const alerts = [
    { id: '1', state: 'OPEN', severity: 'critical', message: 'A', alert_type: 'X' },
    { id: '1', state: 'OPEN', severity: 'critical', message: 'dup', alert_type: 'X' },
    { id: '2', state: 'RESOLVED', severity: 'critical', message: 'gone', alert_type: 'X' },
    { id: '3', state: 'OPEN', severity: 'warn', message: 'w', alert_type: 'DATE_EXPIRY' },
  ]
  const summaries = [
    { target_code: 't1', buckets: { UNKNOWN: 7 }, last_success_at: null,
      data_source: 'platform' },
    { target_code: 't2', buckets: { UNKNOWN: 0 }, last_success_at: 'x',
      data_source: 'platform' },
  ]
  const targets = [
    { code: 't1', name: '业务一', sole_pusher: 'CONFLICT' },
    { code: 't2', name: '业务二', sole_pusher: 'CONFIRMED' },
  ]
  const todos = buildTodos(alerts, summaries, targets)
  assert.equal(todos.length, 5)          // danger(告警1+冲突) + unknown + nosuccess + expiry
  assert.equal(todos[0].tone, 'danger')
  const keys = todos.map((t) => t.key)
  assert.equal(new Set(keys).size, keys.length)   // 无重复
})

test('buildTodos：无数据源(data_source=none)不产生"无确认"待办', () => {
  const todos = buildTodos([], [
    { target_code: 't', buckets: {}, last_success_at: null, data_source: 'none' },
  ], [{ code: 't', name: 'n', sole_pusher: 'UNCONFIRMED' }])
  assert.equal(todos.length, 0)
})

// ---------------- stackDaily ----------------
test('stackDaily 合并多业务 daily 并按日期排序', () => {
  const out = stackDaily([
    { target_code: 'a', daily: [
      { date: '2026-11-10', CONFIRMED: 3, REJECTED: 1 },
      { date: '2026-11-11', CONFIRMED: 2 } ] },
    { target_code: 'b', daily: [
      { date: '2026-11-10', CONFIRMED: 4, UNKNOWN: 5 } ] },
  ])
  // PT-03：堆叠序列补绘 ACCEPTED（已受理）序列
  assert.deepEqual(out, [
    { date: '2026-11-10', CONFIRMED: 7, ACCEPTED: 0, REJECTED: 1, UNKNOWN: 5 },
    { date: '2026-11-11', CONFIRMED: 2, ACCEPTED: 0, REJECTED: 0, UNKNOWN: 0 },
  ])
})

// ---------------- describeSchedule ----------------
test('describeSchedule：镜像/停用/下次触发文案', () => {
  assert.match(describeSchedule(
    { observe_only: true, enabled: false, next_fire_at: null }).label, /镜像/)
  assert.match(describeSchedule(
    { observe_only: false, enabled: false, next_fire_at: null }).label, /停用/)
  const s = describeSchedule(
    { observe_only: false, enabled: true, next_fire_at: '2026-11-11T02:00:00' },
    '2026-11-11T01:00:00Z')
  assert.match(s.label, /下次/)
  assert.equal(s.tone, 'ok')
})

// ---------------- params ----------------
// PT-02：新增 WATERMARK_STALE / PARENT_WAIT_OVER / WATCHDOG_STALE 后共 12 类
test('RULE_FORMS 覆盖 12 种 kind', () => {
  assert.equal(RULE_FORMS.length, 12)
})

test('formToParams 数值转换与范围校验', () => {
  assert.deepEqual(formToParams('NO_SUCCESS_WITHIN', { hours: '30' }),
    { hours: 30 })
  assert.throws(() => formToParams('NO_SUCCESS_WITHIN', { hours: 'x' }),
    /数字/)
  assert.throws(() => formToParams('NO_SUCCESS_WITHIN', { hours: '0' }),
    /不小于/)
  assert.throws(() => formToParams('NO_SUCCESS_WITHIN', {}),
    /必填/)
})

test('formToParams/paramsToForm 互逆（REJECT_RATE_OVER）', () => {
  const v = { rate: '0.05', min_total: '50' }
  const p = formToParams('REJECT_RATE_OVER', v)
  assert.deepEqual(p, { rate: 0.05, min_total: 50 })
  assert.deepEqual(paramsToForm('REJECT_RATE_OVER', p), v)
})

test('formToParams 非必填留空不产生键', () => {
  const p = formToParams('DATE_EXPIRY',
    { label: '药品发送授权', warn_days: '7' })
  assert.equal('source' in p, false)
  assert.deepEqual(p, { label: '药品发送授权', warn_days: 7 })
})

// ---------------- RT-01 回归：双 PRODUCTION 仅 target_code（默认种子白屏 P0）
test('RT-01 sortTargets：两个 PRODUCTION 且仅有 target_code 无 code 字段排序不抛错', () => {
  const rows = [
    { target_code: 'alihealth_drugtrace_yljg', lifecycle: 'PRODUCTION' },
    { target_code: 'inf_cdc_frontend', lifecycle: 'PRODUCTION' },
    { target_code: 'medins_emr_sd', lifecycle: 'MOCK_ONLY' },
    { lifecycle: undefined },                       // 连 code/target_code 都缺也不抛错
  ]
  const out = sortTargets(rows)
  assert.equal(out.length, 4)
  assert.equal(out[0].target_code, 'alihealth_drugtrace_yljg')   // 次键稳定序
  assert.equal(out[out.length - 1].lifecycle, undefined)          // 未知靠后
})
