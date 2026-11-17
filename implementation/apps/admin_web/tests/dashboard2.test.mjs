// LT-06B 纯函数单测补充（与 dashboard.test.mjs 合计 ≥15 新增）。
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  sortTargets, buildTodos, stackDaily, describeSchedule, LIFECYCLE_ORDER,
} from '../src/utils/dashboard.mjs'
import { RULE_FORMS, formToParams, paramsToForm } from '../src/utils/params.mjs'

// dashboard —— 边界与口径
test('LIFECYCLE_ORDER 完整含 SUSPENDED/RETIRED', () => {
  assert.equal(Object.keys(LIFECYCLE_ORDER).length, 7)
  assert.ok(LIFECYCLE_ORDER.PRODUCTION < LIFECYCLE_ORDER.PLANNED)
})

test('sortTargets 空数组/同 lifecycle 按 code', () => {
  assert.deepEqual(sortTargets([]), [])
  assert.deepEqual(
    sortTargets([{ code: 'b', lifecycle: 'PILOT' }, { code: 'a', lifecycle: 'PILOT' }])
      .map(t => t.code), ['a', 'b'])
})

test('buildTodos UNKNOWN 跳转深链含 bucket=UNKNOWN', () => {
  const [t] = buildTodos([], [
    { target_code: 'x', buckets: { UNKNOWN: 3 }, last_success_at: 'y',
      data_source: 'platform' }], [])
  assert.equal(t.to, '/ledger?target=x&bucket=UNKNOWN')
})

test('buildTodos 恰好 5 条时截断', () => {
  const summaries = Array.from({ length: 8 }, (_, i) => ({
    target_code: 't' + i, buckets: { UNKNOWN: 1 }, last_success_at: 'y',
    data_source: 'platform' }))
  const todos = buildTodos([], summaries,
    summaries.map(s => ({ code: s.target_code, name: s.target_code,
      sole_pusher: 'UNCONFIRMED' })))
  assert.equal(todos.length, 5)
})

test('buildTodos danger（冲突）排在 warning 前', () => {
  const todos = buildTodos([], [
    { target_code: 't', buckets: { UNKNOWN: 1 }, last_success_at: 'y',
      data_source: 'platform' }], [
    { code: 't', name: '业务', sole_pusher: 'CONFLICT' }])
  assert.equal(todos[0].tone, 'danger')
})

test('stackDaily 空数组/单业务直通', () => {
  assert.deepEqual(stackDaily([]), [])
  assert.deepEqual(stackDaily([
    { target_code: 'a', daily: [{ date: '2026-11-10', CONFIRMED: 2 }] }]),
    // PT-03：序列补 ACCEPTED 字段
    [{ date: '2026-11-10', CONFIRMED: 2, ACCEPTED: 0, REJECTED: 0,
      UNKNOWN: 0 }])
})

test('describeSchedule 到期触发 tone=warn', () => {
  const s = describeSchedule(
    { observe_only: false, enabled: true, next_fire_at: '2026-11-11T00:00:00' },
    '2026-11-11T01:00:00Z')
  assert.equal(s.tone, 'warn')
  assert.match(s.label, /已到期待触发/)
})

test('describeSchedule 非法时间戳回退原文', () => {
  const s = describeSchedule(
    { observe_only: false, enabled: true, next_fire_at: 'not-a-date' })
  assert.match(s.label, /not-a-date/)
})

// params —— 全 kind 与边界
test('RULE_FORMS 字段类型合法（number/text）', () => {
  for (const f of RULE_FORMS) {
    for (const fd of f.fields) {
      assert.ok(['number', 'text', 'date'].includes(fd.type), f.kind)
    }
  }
})

test('formToParams SCHEDULE_SKIPPED 组合校验', () => {
  assert.deepEqual(
    formToParams('SCHEDULE_SKIPPED',
      { schedule_code: 'inf.poll_mirror', consecutive: '3' }),
    { schedule_code: 'inf.poll_mirror', consecutive: 3 })
  assert.throws(() => formToParams('SCHEDULE_SKIPPED',
    { schedule_code: 'x', consecutive: '101' }), /不大于/)
})

test('formToParams 未知 kind 拒绝', () => {
  assert.throws(() => formToParams('NOPE', {}), /未知规则类型/)
})

test('paramsToForm 未知 kind 返回空对象', () => {
  assert.deepEqual(paramsToForm('NOPE', { a: 1 }), {})
})

test('formToParams 空串视同未填（非必填跳过/必填报错）', () => {
  assert.deepEqual(formToParams('BACKLOG_OVER', { threshold: '10', x: '' }),
    { threshold: 10 })
  assert.throws(() => formToParams('BACKLOG_OVER', { threshold: '  ' }), /必填/)
})

test('formToParams rate=0 合法（0 允许）、rate=1 合法', () => {
  assert.deepEqual(formToParams('REJECT_RATE_OVER', { rate: '0', min_total: '5' }),
    { rate: 0, min_total: 5 })
  assert.deepEqual(formToParams('REJECT_RATE_OVER', { rate: '1', min_total: '5' }),
    { rate: 1, min_total: 5 })
})
