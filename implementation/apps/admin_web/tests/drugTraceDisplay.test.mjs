import test from 'node:test'
import assert from 'node:assert/strict'
import { label, number, totals, hospitalDate, nextSchedule, reportHealth, candidateText, collectionFailure, collectionProgress } from '../src/views/drugTraceDisplay.ts'

test('unknown values stay visible and missing numbers differ from zero', () => {
  assert.equal(label('ACCEPTED'), '平台已受理')
  assert.equal(label('CONFIRMED'), '已确认成功')
  assert.equal(label('NEW_STATE'), 'NEW_STATE')
  assert.equal(number(null), '—')
  assert.equal(number(0), '0')
  assert.equal(number(1248), '1,248')
})
test('submission aggregates include all states without relabeling accepted as success', () => {
  assert.deepEqual(totals([{ total: 5, by_state: { CONFIRMED: 1, ACCEPTED: 2, UNKNOWN: 1, PREPARED: 1 } },
    { total: 3, by_state: { CONFIRMED: 1, PARTIAL: 1, SUPERSEDED: 1 } }]),
    { total: 8, CONFIRMED: 2, ACCEPTED: 2, UNKNOWN: 1, PREPARED: 1, PARTIAL: 1, SUPERSEDED: 1 })
})
test('hospital dates and next scheduled time use GMT+8, not browser timezone or last report', () => {
  assert.equal(hospitalDate(new Date('2026-10-25T22:00:00Z')), '2026-10-26')
  assert.equal(nextSchedule(new Date('2026-10-25T22:00:00Z')), '2026-10-26 06:10')
  assert.equal(nextSchedule(new Date('2026-10-25T22:15:00Z')), '2026-10-27 06:10')
})
test('missing report and streak use actual adjacent hospital business dates', () => {
  const r = reportHealth([{report_date:'2026-10-24', source:'timer', outcome:'OK'},
    {report_date:'2026-10-23', source:'timer', outcome:'OK'},
    {report_date:'2026-10-21', source:'timer', outcome:'OK'},
    {report_date:'2026-10-25', source:'manual', outcome:'OK'}], new Date('2026-10-26T00:00:00Z'))
  assert.equal(r.missing, true)
  assert.equal(r.streak, 2)
  assert.equal(r.yesterdayStr, '2026-10-25')
  assert.equal(r.nextAt, '2026-10-27 06:10')
})
test('no records are not presented as proof of scheduler health', () => {
  const r=reportHealth([],new Date('2026-10-26T00:00:00Z'))
  assert.equal(r.empty,true)
  assert.equal(r.latest,null)
  assert.equal(r.missing,false)
  assert.equal(r.streak,0)
})
test('unknown candidate counts never masquerade as zero, including legacy failed receipts', () => {
  assert.equal(candidateText({ outcome: 'FAILED', candidate_count: 0, collection: { candidate_count_known: false } }), '未统计')
  assert.equal(candidateText({ outcome: 'FAILED', candidate_count: 0 }), '待核实')
  assert.equal(candidateText({ outcome: 'FAILED', candidate_count: 0, collection: { state: 'DONE' } }), '0')
  assert.equal(candidateText({ outcome: 'OK', candidate_count: 0 }), '0')
  assert.equal(candidateText({ outcome: 'FAILED', candidate_count: 1995 }), '1,995')
  assert.equal(candidateText({ outcome: 'FAILED' }), '—')
})
test('collection failure reasons and snapshot progress do not imply current source or platform state', () => {
  const report = { outcome: 'FAILED', candidate_count: 0, collection: { state: 'RUNNING', error_code: 'collect_timeout',
    candidate_count_known: false, summary: { bills_seen: 3337, valid: 1995, waiting: 1342 } } }
  assert.equal(collectionFailure(report), '等待采集完成超时')
  assert.equal(collectionProgress(report), '已采集3,337张，可上报1,995张，待完善1,342张')
  assert.equal(collectionFailure({ outcome: 'FAILED', candidate_count: 0 }), '失败回执缺少采集阶段详情，候选数量尚未核实')
  assert.equal(collectionFailure({ outcome: 'OK', candidate_count: 0 }), '')
  assert.equal(collectionFailure({ outcome: 'PARTIAL_FAILURE', candidate_count: 12, collection: { state: 'DONE' } }), '')
  assert.equal(collectionProgress({ collection: { summary: { bills_seen: 0, valid: -1, waiting: null } } }), '已采集0张')
})
