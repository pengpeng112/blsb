// dashboard.mjs 的类型声明（逻辑唯一事实源；TS 由 dashboard.ts re-export）。
export interface Todo { key: string, tone: 'danger' | 'warning', text: string, to?: string }
export const LIFECYCLE_ORDER: Record<string, number>
export function targetCodeOf(t: { code?: string, target_code?: string }): string
export function sortTargets<T extends { code?: string, target_code?: string, lifecycle?: string }>(targets: T[]): T[]
export function buildTodos(alerts: any[], summaries: any[], targets: {
  code: string, name: string, sole_pusher: string }[]): Todo[]
export function stackDaily(summaries: any[]): Record<string, any>[]
export function describeSchedule(s: {
  observe_only: boolean, enabled: boolean, next_fire_at: string | null },
  nowIso?: string): { label: string, tone: 'muted' | 'ok' | 'warn' }

// PT-03：主成功指标口径（已确认/已受理自适应 + 前置机受理角标）
export function primarySuccessMetric(s: any):
  { value: number, label: string, badge: string }
