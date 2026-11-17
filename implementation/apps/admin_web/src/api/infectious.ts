// PT-03：传染病全维监控（/api/v1/infectious/monitor/*，target_scopes 鉴权）。
import { core } from './http'
export interface InfTableShift {
  rows: number
  sent: number
  parent_wait: number
  skipped_invalid: number
  failed: number
  error: string
}
export interface InfTableReconcile {
  day: string
  repushed: number
  parent_wait: number
  failed: number
}
export interface InfTableRow {
  table: string
  doc_type_code: string
  doc_type_name: string
  endpoint: string
  parent_table: string
  parent_name: string
  watermark: string | null
  lag_hours: number | null
  last_shift_at: string | null
  shift: InfTableShift
  reconcile: InfTableReconcile
  accepted: number
  parent_waiting: number
  rejected: number
  unknown: number
  total: number
}
export interface InfTableGroup { group: string, tables: InfTableRow[] }
export interface InfOverview {
  days: number
  buckets: Record<string, number>
  error_codes: { code: string, count: number, meaning: string }[]
  daily: { date: string, ACCEPTED?: number, REJECTED?: number, UNKNOWN?: number,
           PENDING?: number }[]
  observed_until: string | null
  last_shift_at: string | null
  max_watermark_lag_hours: number | null
  watchdog_level: string | null
  watchdog_checked_at: string | null
  wecom_relay_ok: boolean | null
  sync_configured: boolean
}
export interface InfWatchdogCheck { level: string, elapsed_ms: number, detail: string }
export interface InfWatchdog {
  present: boolean
  checked_at: string | null
  host_label: string
  overall_level: string
  relay_reachable: boolean | null
  checks: Record<string, InfWatchdogCheck>
  driver_errors: Record<string, any>
  daily: { ymd: string, text: string | null }
  recent_alerts: { ts: string, level: string, subject: string }[]
}
export interface InfReconcileRow {
  table: string, doc_type_name: string, day: string,
  repushed: number, parent_wait: number, failed: number
}
export interface InfReconcile {
  days: number
  reconcile: InfReconcileRow[]
  shadow_diff: Record<string, any>
}
export interface InfSyncResult {
  configured: boolean
  reason?: string
  files_scanned?: number
  rows_read?: number
  records_created?: number
  records_updated?: number
  sent_only_records?: number
  snapshots_parsed?: number
  cursors?: { file: string, from: number }[]
  errors?: { file?: string, error: string }[]
}

export function infOverview(days = 7, sourceInstance?: string):
  Promise<InfOverview> {
  const q: Record<string, string | number> = { days }
  if (sourceInstance) q.source_instance = sourceInstance
  return core.get('/api/v1/infectious/monitor/overview', q as any)
}
export function infTables(days = 7, sourceInstance?: string):
  Promise<{ days: number, groups: InfTableGroup[], total_tables: number }> {
  const q: Record<string, string | number> = { days }
  if (sourceInstance) q.source_instance = sourceInstance
  return core.get('/api/v1/infectious/monitor/tables', q as any)
}
export function infWatchdog(sourceInstance?: string): Promise<InfWatchdog> {
  return core.get('/api/v1/infectious/monitor/watchdog',
    sourceInstance ? { source_instance: sourceInstance } as any : undefined)
}
export function infReconcile(days = 7, sourceInstance?: string):
  Promise<InfReconcile> {
  const q: Record<string, string | number> = { days }
  if (sourceInstance) q.source_instance = sourceInstance
  return core.get('/api/v1/infectious/monitor/reconcile', q as any)
}
export function infSync(): Promise<InfSyncResult> {
  return core.post('/api/v1/infectious/monitor/sync', {})
}
