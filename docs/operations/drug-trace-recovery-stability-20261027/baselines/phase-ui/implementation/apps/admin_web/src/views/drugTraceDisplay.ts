/** Presentation helpers only. Counts represent submissions, not unique bills. */
export const labels: Record<string, string> = {
  CONFIRMED: '已确认成功', ACCEPTED: '平台已受理', UNKNOWN: '结果待核查',
  PARTIAL: '部分成功', REJECTED: '平台退回', PREPARED: '待发送', SENDING: '发送中',
  SUPERSEDED: '旧版本已替代', VALID: '可上报', WAITING: '待完善', INVALID: '校验未通过',
  OK: '正常完成', PARTIAL_FAILURE: '部分完成', FAILED: '执行失败', DONE: '已完成',
  PENDING: '等待处理', RUNNING: '处理中', CANCELLED: '已取消', COMPLETE: '对账完成',
  APPROVED: '已授权', MISSING: '未登记', REVOKED: '已撤销', EXPIRED: '已过期',
  PATIENT_DISPENSE: '患者发药', PATIENT_RETURN: '患者退药', SUPPLIER_IN: '采购入库',
  SUPPLIER_RETURN: '供应商退货', timer: '定时任务', manual: '人工核查',
  collect: '数据采集', send: '上报发送', query: '结果查询', reconcile: '平台对账',
}
export function label(value?: string): string { return value ? labels[value] || value : '—' }
export function number(value: number | null | undefined): string {
  return value == null ? '—' : new Intl.NumberFormat('zh-CN').format(value)
}
export function totals(days: any[]): Record<string, number> {
  const result: Record<string, number> = { total: 0 }
  for (const day of days) {
    result.total += Number(day.total || 0)
    for (const [state, count] of Object.entries(day.by_state || {})) result[state] = (result[state] || 0) + Number(count)
  }
  return result
}
/** All scheduler dates are hospital GMT+8, independent of browser timezone. */
export function hospitalDate(now = new Date()): string {
  return new Date(now.getTime() + 8 * 3600_000).toISOString().slice(0, 10)
}
export function nextSchedule(now = new Date()): string {
  const shifted = new Date(now.getTime() + 8 * 3600_000)
  const minutes = shifted.getUTCHours() * 60 + shifted.getUTCMinutes()
  if (minutes >= 6 * 60 + 10) shifted.setUTCDate(shifted.getUTCDate() + 1)
  return shifted.toISOString().slice(0, 10) + ' 06:10'
}
export function reportHealth(rows: any[], now = new Date()) {
  const timer = rows.filter(r => r.source === 'timer').slice().sort((a, b) => b.report_date.localeCompare(a.report_date))
  const nextAt = nextSchedule(now)
  if (!timer.length) return { empty: true, nextAt, streak: 0, missing: false, yesterdayStr: '', latest: null }
  const map = new Map(timer.map(r => [r.report_date, r.outcome]))
  let streak = 0
  const cursor = new Date(timer[0].report_date + 'T00:00:00Z')
  while (map.get(cursor.toISOString().slice(0, 10)) === 'OK') { streak++; cursor.setUTCDate(cursor.getUTCDate() - 1) }
  const shifted = new Date(now.getTime() + 8 * 3600_000)
  const yesterday = new Date(shifted); yesterday.setUTCDate(yesterday.getUTCDate() - 1)
  const yesterdayStr = yesterday.toISOString().slice(0, 10)
  return { empty: false, nextAt, streak, latest: timer[0], yesterdayStr,
    missing: !timer.some(r => r.report_date === yesterdayStr) && shifted.getUTCHours() * 60 + shifted.getUTCMinutes() > 450 }
}
