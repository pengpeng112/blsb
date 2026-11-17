// LT-02 API 类型（字段名与 LT-01 后端 report_target 端点一致）。
export type Bucket = 'PENDING' | 'SENDING' | 'ACCEPTED' | 'CONFIRMED' |
  'PARTIAL' | 'REJECTED' | 'UNKNOWN' | 'NOT_REPORTABLE'

export interface ReportTarget {
  code: string
  name: string
  category: string
  authority: string
  plugin_key: string
  destination_id: string | null
  mode: 'PUSH' | 'PULL' | 'HYBRID'
  lifecycle: 'PLANNED' | 'DEVELOPING' | 'MOCK_ONLY' | 'PILOT' | 'PRODUCTION' |
    'SUSPENDED' | 'RETIRED'
  runtime_kind: 'PLATFORM' | 'EXTERNAL' | 'MANUAL'
  owner_dept: string
  contact: string
  sole_pusher: 'CONFIRMED' | 'UNCONFIRMED' | 'CONFLICT'
  sole_pusher_note: string
  spec_docs: string[]
  readme_path: string
  schedule_note: string
  extra: Record<string, unknown>
  revision: number
  updated_by: string
  updated_at: string | null
  /** RT-01：绑定目的地时后端附带的只读脱敏摘要（get_target 同次返回） */
  destination_summary?: {
    id: string
    name: string
    mode: string
    status: string
    is_mock: boolean
    profile_code: string
    host_port: string | null
    receipt_rules: unknown[]
  }
}

export interface TargetSummary {
  target_code: string
  days: number
  buckets: Record<string, number>
  daily: Record<string, any>[]
  last_success_at: string | null
  backlog: number
  unmapped_states: Record<string, number>
  data_source: 'platform' | 'external_ledger' | 'none'
  note: string
  /** LT-03-R1 口径分离字段（后端未升级时 undefined——页面各自兜底显示） */
  window_kind?: 'rolling_days' | 'hospital_day'
  backlog_total?: number | null
  quality_waiting?: number
  evidence_kind?: '' | 'real' | 'mock' | 'mixed'
  /** overview 聚合时附加的目录字段与局部错误 */
  name?: string
  lifecycle?: string
  runtime_kind?: string
  sole_pusher?: string
  error?: string
}

export interface Paged<T> { items: T[]; next_cursor?: string | null }

export interface SessionInfo {
  access_token?: string
  code: string            // 后端 login/me 总返回（旧 Login.vue 直接 s.display_name || s.code）
  display_name: string
  roles?: string[]
  password_session?: boolean
  /** RT-01：业务授权范围（/api/v1/auth/me 返回）；null/缺省=不受限（admin/机器主体）。 */
  target_scopes?: string[] | null
}

// ---------------- LT-04/LT-05/LT-06A 运营对象 ----------------
export interface ScheduleItem {
  id: string; code: string; name: string; target_code: string
  job_type: string; payload_template: Record<string, unknown>
  cron: string; enabled: boolean; observe_only: boolean
  misfire_policy: string; concurrency: string
  max_runtime_seconds: number
  blackout: { start: string, end: string }[]
  next_fire_at: string | null; last_fire_at: string | null
  revision: number; note: string
  updated_at: string | null; updated_by: string
}
export interface ScheduleRun {
  id: string; schedule_id: string; run_key: string
  fire_time: string | null; state: string; job_id: string | null
  started_at: string | null; ended_at: string | null
  deadline_at: string | null; duration_seconds: number | null
  result_summary: Record<string, any>
  error_code: string; error_message: string
  trigger: string; triggered_by: string
}
export interface AlertItem {
  id: string; alert_type: string; severity: string; message: string
  target_ref: string; state: string; created_at: string | null
  fingerprint: string | null; rule_code: string
  target_code: string
  first_seen_at: string | null; last_seen_at: string | null
  occurrences: number | null
  acked_by: string; acked_at: string | null; resolved_at: string | null
  detail: Record<string, any>
}
export interface AlertRuleItem {
  code: string; name: string; target_code: string
  kind: string; params: Record<string, any>
  severity: string; enabled: boolean
  cooldown_minutes: number; notify_channels: string[]
  revision: number; updated_at: string | null
}
export type NotifyChannelType =
  | 'file' | 'smtp' | 'webhook' | 'wecom_relay' | 'wecom_robot'
export interface NotifyChannel {
  code: string; type: NotifyChannelType
  config: Record<string, any>; enabled: boolean
  updated_at: string | null
}
export interface NotifyLog {
  id: string
  channel: string; ok: boolean; error: string
  kind: string; sent_at: string
}
export interface LedgerRow {
  id: string; target_code: string; business_key: string
  bucket: string; raw_state: string
  created_at: string; updated_at: string
  attempts: number
  last_error_code: string | null; last_error_message: string
  detail_route?: string | null
}
export interface LedgerPageData {
  target: string
  items: LedgerRow[]
  next_cursor: string | null
  total_estimate: number | null
  data_source: string
}
export interface ExternalImportBatchItem {
  id: string; target: string; source_label: string
  source_instance: string; source_tz: string
  files: { name: string, sha256: string, rows: number }[]
  rows_read: number; rows_applied: number
  rows_skipped_duplicate: number; rows_rejected: number
  started_at: string; ended_at: string | null
  imported_by: string; dry_run: boolean
}
export interface ConsoleUser {
  username: string; subject_id: string; display_name: string
  roles: string[]; target_scopes: string[]
  status: string; locked: boolean; locked_until: string | null
  password_changed_at: string; created_at: string
}
export interface AuditRow {
  id: string; actor: string; action: string
  target: string; reason: string; at: string
}
