// LT-02 前端工具函数单元测试（node:test 直跑 TS，与 drugTraceDisplay.test.mjs 同方式）。
import test from 'node:test'
import assert from 'node:assert/strict'
import { formatHospitalTime, relativeTime, daysAgoRange } from '../src/utils/time.ts'
import { formatBytes } from '../src/utils/bytes.ts'
import { BUCKET_LABEL, BUCKET_TONE, lifecycleLabel, runtimeKindLabel, solePusherLabel, bucketLabel, bucketTone } from '../src/utils/status.ts'
import { successRate } from '../src/utils/rate.ts'
import { filterMenuByRoles } from '../src/utils/menu.ts'

// ---------------- time ----------------
test('formatHospitalTime fixes UTC+8 regardless of browser timezone', () => {
  assert.equal(formatHospitalTime('2026-11-08T02:00:00Z'), '2026-11-08 10:00:00')
  assert.equal(formatHospitalTime('2026-11-08T15:30:05Z'), '2026-11-08 23:30:05')
})
test('formatHospitalTime accepts Date and invalid values', () => {
  assert.equal(formatHospitalTime(new Date('2026-01-02T16:00:00Z')), '2026-01-03 00:00:00')
  assert.equal(formatHospitalTime('not-a-date'), '—')
  assert.equal(formatHospitalTime(null), '—')
  assert.equal(formatHospitalTime(''), '—')
})
test('relativeTime buckets by minutes/hours/days', () => {
  const now = new Date('2026-11-08T04:00:00Z')
  assert.equal(relativeTime('2026-11-08T03:59:40Z', now), '刚刚')
  assert.equal(relativeTime('2026-11-08T03:45:00Z', now), '15 分钟前')
  assert.equal(relativeTime('2026-11-08T01:00:00Z', now), '3 小时前')
  assert.equal(relativeTime('2026-11-06T04:00:00Z', now), '2 天前')
})
test('relativeTime falls back to hospital date beyond 30 days and handles bad input', () => {
  const now = new Date('2026-12-15T04:00:00Z')
  assert.equal(relativeTime('2026-11-01T00:00:00Z', now).startsWith('2026-11-01'), true)
  assert.equal(relativeTime('zzz', now), '—')
})
test('daysAgoRange produces half-open [now-n days, now) window', () => {
  const r = daysAgoRange(7, new Date('2026-11-08T04:00:00Z'))
  const b = new Date(r.begin), e = new Date(r.end)
  assert.equal((e.getTime() - b.getTime()) / 86400000, 7)
  assert.equal(r.end, '2026-11-08T04:00:00.000Z')
  assert.equal(r.begin, '2026-11-01T04:00:00.000Z')
})

// ---------------- bytes ----------------
test('formatBytes uses 1024 base with one decimal', () => {
  assert.equal(formatBytes(512), '512 B')
  assert.equal(formatBytes(2048), '2.0 KB')
  assert.equal(formatBytes(1536), '1.5 KB')
  assert.equal(formatBytes(5 * 1024 * 1024), '5.0 MB')
  assert.equal(formatBytes(3.5 * 1024 ** 3), '3.5 GB')
})
test('formatBytes null/invalid shows dash', () => {
  assert.equal(formatBytes(null), '—')
  assert.equal(formatBytes(undefined), '—')
  assert.equal(formatBytes(-5), '—')
  assert.equal(formatBytes(NaN), '—')
})

// ---------------- status ----------------
test('bucket labels and tones match backend REPORT_BUCKETS', () => {
  const buckets = ['PENDING', 'SENDING', 'ACCEPTED', 'CONFIRMED', 'PARTIAL',
    'REJECTED', 'UNKNOWN', 'NOT_REPORTABLE']
  for (const b of buckets) {
    assert.equal(typeof BUCKET_LABEL[b], 'string')
    assert.equal(typeof BUCKET_TONE[b], 'string')
  }
  assert.equal(BUCKET_TONE.CONFIRMED, 'success')
  assert.equal(BUCKET_TONE.REJECTED, 'danger')
  assert.equal(BUCKET_TONE.NOT_REPORTABLE, 'muted')
  assert.equal(BUCKET_LABEL.PENDING, '待发送')
})
test('unknown state values stay visible', () => {
  assert.equal(bucketLabel('SUPERSEDED'), 'SUPERSEDED')
  assert.equal(bucketTone('SUPERSEDED'), 'muted')
  assert.equal(bucketLabel(null), '—')
})
test('lifecycle/runtime/solePusher chinese labels', () => {
  assert.equal(lifecycleLabel('PRODUCTION'), '正式')
  assert.equal(lifecycleLabel('MOCK_ONLY'), '仅 Mock')
  assert.equal(lifecycleLabel('WEIRD'), 'WEIRD')
  assert.equal(runtimeKindLabel('EXTERNAL'), '平台外推送')
  assert.equal(solePusherLabel('CONFLICT'), '唯一推送方冲突')
  assert.equal(lifecycleLabel(''), '—')
})

// ---------------- rate ----------------
test('successRate: zero denominator returns null (never shows 100%)', () => {
  assert.equal(successRate({}), null)
  assert.equal(successRate({ CONFIRMED: 0, REJECTED: 0, PARTIAL: 0, UNKNOWN: 0 }), null)
  assert.equal(successRate(null), null)
})
test('successRate: UNKNOWN counts into denominator, not success', () => {
  assert.equal(successRate({ CONFIRMED: 3, REJECTED: 1, PARTIAL: 0, UNKNOWN: 1 }), 0.6)
  assert.equal(successRate({ CONFIRMED: 0, UNKNOWN: 5 }), 0)
  assert.equal(successRate({ CONFIRMED: 10 }), 1)
  assert.equal(successRate({ REJECTED: 2 }), 0)
})

// ---------------- menu ----------------
const MENU = [
  { key: 'g1', label: '工作台', items: [{ path: '/overview', label: '平台总览' }] },
  { key: 'g2', label: '接入配置', items: [
    { path: '/connectors', label: '连接器', roles: ['platform_admin', 'config_editor'] },
    { path: '/mapping', label: '字段映射', roles: ['platform_admin', 'config_editor'] }] },
  { key: 'g3', label: '资产', items: [
    { path: '/devices', label: '设备', roles: ['platform_admin', 'operator'] }] },
  { key: 'g4', label: '系统', items: [
    { path: '/audit', label: '审计', roles: ['platform_admin', 'auditor'] }] },
]
test('platform_admin sees everything', () => {
  const out = filterMenuByRoles(MENU, ['platform_admin'])
  assert.equal(out.length, 4)
  assert.equal(out[1].items.length, 2)
})
test('operator cannot see 接入配置 group (验收清单)', () => {
  const out = filterMenuByRoles(MENU, ['operator'])
  assert.equal(out.some(g => g.key === 'g2'), false)
  const g3 = out.find(g => g.key === 'g3')
  assert.equal(g3.items.length, 1)
})
test('auditor sees only workspace and system', () => {
  const out = filterMenuByRoles(MENU, ['auditor'])
  assert.deepEqual(out.map(g => g.key), ['g1', 'g4'])
})
test('empty groups are hidden after filtering', () => {
  const m = [{ key: 'x', label: 'X', items: [{ path: '/p', label: 'P', roles: ['auditor'] }] }]
  assert.deepEqual(filterMenuByRoles(m, ['operator']), [])
})
test('items without roles visible to any logged-in role', () => {
  const out = filterMenuByRoles(MENU, ['business_reviewer'])
  assert.equal(out.length, 1)
  assert.equal(out[0].key, 'g1')
})

// ---------------- 补充边界用例（LT-02 要求新增 ≥20） ----------------
test('formatHospitalTime accepts values already carrying +08:00 offset', () => {
  assert.equal(formatHospitalTime('2026-11-08T10:00:00+08:00'), '2026-11-08 10:00:00')
  assert.equal(formatHospitalTime('2026-11-08T18:00:00+08:00'), '2026-11-08 18:00:00')
})
test('formatBytes boundary 1023/1024 and zero', () => {
  assert.equal(formatBytes(1023), '1023 B')
  assert.equal(formatBytes(1024), '1.0 KB')
  assert.equal(formatBytes(0), '0 B')
})
test('successRate uses only settled outcomes, ignores pending/accepted buckets', () => {
  // ACCEPTED 不在分母（未终态）；只有 CONFIRMED/REJECTED/PARTIAL/UNKNOWN 计
  assert.equal(successRate({ CONFIRMED: 1, ACCEPTED: 999 }), 1)
  assert.equal(successRate({ PARTIAL: 1, REJECTED: 1 }), 0)
  assert.equal(successRate({ CONFIRMED: 7, REJECTED: 3 }), 0.7)
})
test('filterMenuByRoles intersects multiple roles per item', () => {
  const m = [{ key: 'g', label: 'G', items: [
    { path: '/a', label: 'A', roles: ['operator', 'auditor'] },
    { path: '/b', label: 'B', roles: ['config_editor'] } ] }]
  const out = filterMenuByRoles(m, ['auditor', 'config_editor'])
  assert.deepEqual(out[0].items.map(i => i.path), ['/a', '/b'])
})
