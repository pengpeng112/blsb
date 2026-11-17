// LT-04/LT-05/LT-06A 运营端点（调度/告警/台账/导入/用户/审计）。
// 后端能力未就绪时调用 4xx/5xx——页面显示 ErrorState，不伪造数据。
import { ApiError, clearLogin, core, coreBase, token } from './http'
import type {
  AlertItem, AlertRuleItem, AuditRow, ConsoleUser, ExternalImportBatchItem,
  LedgerPageData, NotifyChannel, NotifyLog, ScheduleItem, ScheduleRun,
} from './types'

// ---------------- 调度 ----------------
export function listSchedules(target?: string): Promise<ScheduleItem[]> {
  return core.get('/api/v1/schedules', target ? { target } : undefined)
}
/** RT-03 S08：真实 keyset 分页（fire_time DESC, id DESC）；next_cursor 为
 *  null=末页。 */
export function scheduleRuns(id: string, limit = 50, cursor?: string):
  Promise<{ schedule: string, items: ScheduleRun[],
    next_cursor: string | null }> {
  const q: Record<string, string | number> = { limit }
  if (cursor) q.cursor = cursor
  return core.get(`/api/v1/schedules/${id}/runs`, q as any)
}
export function cronPreview(cron: string, count = 5):
  Promise<{ cron: string, next: string[] }> {
  return core.get('/api/v1/schedules/preview', { cron, count })
}
export function enableSchedule(id: string, reason: string) {
  return core.post(`/api/v1/schedules/${id}/enable`, { reason })
}
export function disableSchedule(id: string, reason: string) {
  return core.post(`/api/v1/schedules/${id}/disable`, { reason })
}
export function runNow(id: string, reason: string, idempotencyKey?: string):
  Promise<ScheduleRun> {
  return core.post(`/api/v1/schedules/${id}/run-now`,
    { reason, idempotency_key: idempotencyKey })
}

// ---------------- 告警 ----------------
/** RT-03 S08：alerts2 分页对象（原数组迁移）；severity 归一 INFO/WARN/
 *  CRITICAL。实时列表分页：last_seen_at 会变化，前端按 ID 合并去重，
 *  刷新/改筛选重置游标。 */
export function listAlerts(params?: {
  state?: string, severity?: string, target?: string, limit?: number,
  cursor?: string }):
  Promise<{ items: AlertItem[], next_cursor: string | null }> {
  const q: Record<string, string | number> = {}
  for (const k of ['state', 'severity', 'target', 'limit', 'cursor'] as const) {
    const v = params?.[k]
    if (v) q[k] = v
  }
  return core.get('/api/v1/alerts2', q as any)
}
export function ackAlert(id: string, reason: string) {
  return core.post(`/api/v1/alerts2/${id}/ack`, { reason })
}
export function resolveAlert(id: string, reason: string) {
  return core.post(`/api/v1/alerts2/${id}/resolve`, { reason })
}
export function listAlertRules(): Promise<AlertRuleItem[]> {
  return core.get('/api/v1/alert-rules')
}
export function updateAlertRule(code: string, body: Partial<AlertRuleItem> & {
  revision: number }) {
  return core.put(`/api/v1/alert-rules/${encodeURIComponent(code)}`, body)
}
export function listChannels(): Promise<NotifyChannel[]> {
  return core.get('/api/v1/notification-channels')
}
/** PT-02：通道验证三态——dry_run=零出网预览 / probe=网关真达探测 / 缺省=实发测试。 */
export interface ChannelTestPreview {
  type: string
  kind: string
  card_title: string
  card_body: string
  env_status: Record<string, string>
  headers_preview: Record<string, string>
  protocol: string
}
export function testChannel(code: string, opts?: {
  dry_run?: boolean, probe?: boolean,
}): Promise<{ ok: boolean, error?: string, dry_run?: boolean,
              probe?: boolean, detail?: string,
              preview?: ChannelTestPreview }> {
  const q: Record<string, string> = {}
  if (opts?.dry_run) q.dry_run = 'true'
  if (opts?.probe) q.probe = 'true'
  return core.post(`/api/v1/notification-channels/${encodeURIComponent(code)}/test`,
    {}, undefined, undefined, q)
}
/** RT-03 S08：通知日志分页对象（sent_at DESC, id DESC keyset）。 */
export function listNotifyLogs(kind?: string, cursor?: string,
  limit = 50): Promise<{ items: NotifyLog[], next_cursor: string | null }> {
  const q: Record<string, string | number> = { limit }
  if (kind) q.kind = kind
  if (cursor) q.cursor = cursor
  return core.get('/api/v1/notification-logs', q as any)
}
export function latestDigest():
  Promise<{ text: string | null, sent_at: string | null }> {
  return core.get('/api/v1/alerts/digest/latest')
}

// ---------------- 台账（LT-03） ----------------
export function ledgerSearch(target: string, params: {
  bucket?: string, date_from?: string, date_to?: string, q?: string,
  cursor?: string, limit?: number }): Promise<LedgerPageData> {
  const query: Record<string, string | number> = { target, limit: params.limit ?? 50 }
  for (const k of ['bucket', 'date_from', 'date_to', 'q', 'cursor'] as const) {
    const v = params[k]
    if (v) query[k] = v
  }
  return core.get('/api/v1/report-center/submissions', query as any)
}
export function ledgerDetail(target: string, id: string) {
  return core.get(`/api/v1/report-center/submissions/${encodeURIComponent(target)}/${id}`)
}
export function manualResolution(target: string, id: string, body: {
  resolution: 'CONFIRMED' | 'REJECTED' | 'RESEND_ALLOWED',
  reason: string, evidence_ref?: string, expected_state: 'UNKNOWN' }) {
  return core.post(
    `/api/v1/report-center/submissions/${encodeURIComponent(target)}/${id}/manual-resolution`,
    body)
}

// ---------------- 外部导入（LT-05） ----------------
export function listImports(target?: string): Promise<ExternalImportBatchItem[]> {
  return core.get('/api/v1/report-center/external-imports', target ? { target } : undefined)
}
export function importExternal(body: {
  target: string, source_label: string, source_instance?: string,
  source_tz: 'UTC' | 'CST' | 'AUTO_MIXED',
  files: { name: string, content_base64: string }[], dry_run?: boolean }) {
  return core.post('/api/v1/report-center/external-imports', body)
}

// ---------------- 用户与角色（LT-06A） ----------------
export function listUsers(): Promise<ConsoleUser[]> {
  return core.get('/api/v1/system/users')
}
export function createUser(body: {
  username: string, password: string, roles: string[],
  target_scopes?: string[], display_name?: string }) {
  return core.post('/api/v1/system/users', body)
}
export function updateUserRoles(username: string, body: {
  roles: string[], target_scopes: string[] }) {
  return core.put(`/api/v1/system/users/${encodeURIComponent(username)}/roles`, body)
}
export function resetUserPassword(username: string, password: string) {
  return core.post(
    `/api/v1/system/users/${encodeURIComponent(username)}/reset-password`,
    { password })
}
export function lockUser(username: string) {
  return core.post(`/api/v1/system/users/${encodeURIComponent(username)}/lock`, {})
}
export function unlockUser(username: string) {
  return core.post(`/api/v1/system/users/${encodeURIComponent(username)}/unlock`, {})
}

// ---------------- 审计（LT-06A 扩展） ----------------
export function searchAudit(params: {
  actor?: string, action?: string, action_prefix?: string, target?: string,
  at_from?: string, at_to?: string, cursor?: string, limit?: number }):
  Promise<AuditRow[] | { items: AuditRow[], next_cursor: string | null }> {
  const q: Record<string, string | number> = {}
  for (const k of ['actor', 'action', 'action_prefix', 'target', 'at_from',
    'at_to', 'cursor', 'limit'] as const) {
    const v = params[k]
    if (v) q[k] = v
  }
  return core.get('/api/v1/audit', q as any)
}
export function auditDetail(id: string):
  Promise<AuditRow & { old_ref: unknown, new_ref: unknown }> {
  return core.get(`/api/v1/audit/${id}`)
}
export interface AuditExportParams {
  actor?: string, action?: string, action_prefix?: string, target?: string,
  at_from?: string, at_to?: string }

export function auditExportUrl(params: AuditExportParams): string {
  const q = new URLSearchParams()
  for (const k of ['actor', 'action', 'action_prefix', 'target', 'at_from',
    'at_to'] as const) {
    const v = params[k]
    if (v) q.set(k, v)
  }
  return `${coreBase()}/api/v1/audit/export?${q.toString()}`
}

/** RT-01：带 Authorization 头下载审计 CSV。原生 <a href download> 不带
 Bearer 头必返 401；这里用 fetch 取 Blob，失败时抛 ApiError（message 为后端
 中文原因，如超过 10000 行上限）。 */
export async function exportAuditCsv(params: AuditExportParams): Promise<Blob> {
  const r = await fetch(auditExportUrl(params), {
    headers: { Authorization: `Bearer ${token()}` } })
  if (!r.ok) {
    const payload = await r.json().catch(() => ({} as any))
    if (r.status === 401) {
      clearLogin()
      window.dispatchEvent(new Event('blyb:unauthorized'))
    }
    throw new ApiError(r.status, payload?.error?.code ?? 'HTTP_ERROR',
      payload?.error?.message || r.statusText || '导出失败',
      payload?.request_id ?? '', payload)
  }
  return r.blob()
}

// ---------------- RT-05 调度编辑与元数据 ----------------
export interface SchedulesMeta {
  job_types: string[]
  misfire_policies: string[]
  concurrency_modes: string[]
}
export function schedulesMeta(): Promise<SchedulesMeta> {
  return core.get('/api/v1/schedules/meta')
}
export function createSchedule(body: Record<string, unknown>): Promise<ScheduleItem> {
  return core.post('/api/v1/schedules', body)
}
export function updateSchedule(id: string, body: Record<string, unknown>):
  Promise<ScheduleItem> {
  return core.put(`/api/v1/schedules/${id}`, body)
}

// ---------------- RT-05 告警规则/通道 ----------------
export function createAlertRule(body: Record<string, unknown>): Promise<AlertRuleItem> {
  return core.post('/api/v1/alert-rules', body)
}
export function createChannel(body: Record<string, unknown>): Promise<NotifyChannel> {
  return core.post('/api/v1/notification-channels', body)
}
export function updateChannel(code: string, body: Record<string, unknown>):
  Promise<NotifyChannel> {
  return core.put(`/api/v1/notification-channels/${encodeURIComponent(code)}`, body)
}

// ---------------- RT-05 配置包发布（rollback/restore） ----------------
export interface ConfigReleaseItem {
  id: string
  digest: string
  note?: string
  released_by?: string
  released_at?: string | null
  rolled_back?: boolean
  revision_ids?: unknown[]
  entries?: unknown
  test_report?: Record<string, unknown> | null
}
export function listConfigReleases(): Promise<ConfigReleaseItem[]> {
  return core.get('/api/v1/config-releases')
}
export function getConfigRelease(id: string): Promise<ConfigReleaseItem> {
  return core.get(`/api/v1/config-releases/${encodeURIComponent(id)}`)
}
export function rollbackRelease(id: string, reason: string) {
  return core.post(`/api/v1/config-releases/${encodeURIComponent(id)}/rollback`,
    { reason })
}
export function restoreRelease(id: string, reason: string):
  Promise<{ release_id: string, digest: string, source_release_id: string,
    entries: number, idempotent: boolean }> {
  return core.post(`/api/v1/config-releases/${encodeURIComponent(id)}/restore`,
    { reason })
}
