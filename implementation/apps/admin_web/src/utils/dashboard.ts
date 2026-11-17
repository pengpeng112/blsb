// LT-06B 驾驶舱纯函数：类型化 re-export（逻辑唯一事实源在 dashboard.mjs，
// node:test 直接测 mjs；Vue/TS 层从这里 import 保持类型）。
import type { AlertItem, ScheduleItem, TargetSummary } from '../api/types'
import {
  buildTodos as _buildTodos, describeSchedule as _describeSchedule,
  LIFECYCLE_ORDER as _LIFECYCLE_ORDER, sortTargets as _sortTargets,
  primarySuccessMetric as _primarySuccessMetric,
  stackDaily as _stackDaily, targetCodeOf as _targetCodeOf,
} from './dashboard.mjs'

export interface Todo {
  key: string
  tone: 'danger' | 'warning'
  text: string
  to?: string
}

export const LIFECYCLE_ORDER: Record<string, number> = _LIFECYCLE_ORDER

/** RT-01：兼容 TargetSummary.target_code 与 ReportTarget.code 两种字段。 */
export function targetCodeOf(t: { code?: string, target_code?: string }): string {
  return _targetCodeOf(t)
}

export function sortTargets<T extends { code?: string, target_code?: string,
  lifecycle?: string }>(targets: T[]): T[] {
  return _sortTargets(targets) as T[]
}

export function buildTodos(alerts: AlertItem[], summaries: TargetSummary[],
  targets: { code: string, name: string, sole_pusher: string }[]): Todo[] {
  return _buildTodos(alerts, summaries, targets) as Todo[]
}

export function stackDaily(summaries: TargetSummary[]): Record<string, any>[] {
  return _stackDaily(summaries)
}

export function describeSchedule(s: ScheduleItem, nowIso?: string):
  { label: string, tone: 'muted' | 'ok' | 'warn' } {
  return _describeSchedule(s, nowIso)
}

// PT-03：主成功指标口径（已确认/已受理自适应）
export function primarySuccessMetric(s: TargetSummary):
  { value: number, label: string, badge: string } {
  return _primarySuccessMetric(s) as any
}
