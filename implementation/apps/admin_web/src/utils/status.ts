// LT-02 状态→展示映射（与后端 contracts.REPORT_BUCKETS 一一对应）。
// tone 用于 StatusTag：success/warning/danger/info/muted。
// RT-01：BUCKET_LABEL/BUCKET_TONE 在 8 个提交桶之外补齐后端全部枚举
// （生命周期/运行形态/唯一推送方/调度运行/告警/账号/联调/启停），保证
// <StatusTag :status="..."> 永远有中文标签，不再满屏 "—"。
export type Bucket = 'PENDING' | 'SENDING' | 'ACCEPTED' | 'CONFIRMED' |
  'PARTIAL' | 'REJECTED' | 'UNKNOWN' | 'NOT_REPORTABLE'

export const BUCKET_LABEL: Record<string, string> = {
  // ---- 提交桶（contracts.REPORT_BUCKETS）
  PENDING: '待发送', SENDING: '发送中', ACCEPTED: '已受理(待确认)',
  CONFIRMED: '已确认', PARTIAL: '部分成功', REJECTED: '被拒绝',
  UNKNOWN: '结果未知', NOT_REPORTABLE: '不上报',
  // ---- 生命周期 TargetLifecycle
  PRODUCTION: '正式运行', PILOT: '试点运行', MOCK_ONLY: '仅Mock',
  DEVELOPING: '开发中', PLANNED: '已规划', SUSPENDED: '已暂停',
  RETIRED: '已下线',
  // ---- 运行形态 TargetRuntimeKind
  PLATFORM: '平台托管', EXTERNAL: '外部程序', MANUAL: '人工上报',
  // ---- 唯一推送方 sole_pusher
  UNCONFIRMED: '待确认', CONFLICT: '存在双推',
  // ---- 调度运行 ScheduleRun.state
  QUEUED: '排队中', RUNNING: '运行中', SUCCEEDED: '成功',
  FAILED: '失败', TIMED_OUT: '已超时', STALLED: '疑似卡死',
  SKIPPED_OVERLAP: '防重跳过', SKIPPED_BLACKOUT: '维护窗跳过',
  MISSED: '错过跳过', CANCELLED: '已取消',
  // ---- 告警状态与级别
  OPEN: '未处理', ACKED: '已确认(告警)', RESOLVED: '已恢复',
  CRITICAL: '严重', critical: '严重',
  WARN: '警告', warn: '警告', warning: '警告',
  INFO: '提示', info: '提示',
  // ---- 账号与联调
  ACTIVE: '正常', LOCKED: '已锁定', DISABLED: '已停用',
  PASS: '通过', FAIL: '未通过', ERROR: '异常',
  MANUAL_OBSERVATION: '人工回填',
  // ---- 页面自造的启停/观测态（Schedules/Alerts/TargetDetail）
  OBSERVE_ONLY: '仅观测', ENABLED: '已启用', OK: '成功',
}

export const BUCKET_TONE: Record<string, string> = {
  PENDING: 'info', SENDING: 'info', ACCEPTED: 'warning', CONFIRMED: 'success',
  PARTIAL: 'warning', REJECTED: 'danger', UNKNOWN: 'danger',
  NOT_REPORTABLE: 'muted',
  // 生命周期
  PRODUCTION: 'success', PILOT: 'info', MOCK_ONLY: 'mock',
  DEVELOPING: 'warning', PLANNED: 'muted', SUSPENDED: 'warning',
  RETIRED: 'muted',
  // 运行形态
  PLATFORM: 'info', EXTERNAL: 'warning', MANUAL: 'muted',
  // 唯一推送方
  UNCONFIRMED: 'warning', CONFLICT: 'danger',
  // 调度运行
  QUEUED: 'info', RUNNING: 'info', SUCCEEDED: 'success', FAILED: 'danger',
  TIMED_OUT: 'danger', STALLED: 'danger', SKIPPED_OVERLAP: 'warning',
  SKIPPED_BLACKOUT: 'muted', MISSED: 'warning', CANCELLED: 'muted',
  // 告警状态与级别
  OPEN: 'danger', ACKED: 'warning', RESOLVED: 'success',
  CRITICAL: 'danger', critical: 'danger',
  WARN: 'warning', warn: 'warning', warning: 'warning',
  INFO: 'info', info: 'info',
  // 账号与联调
  ACTIVE: 'success', LOCKED: 'danger', DISABLED: 'muted',
  PASS: 'success', FAIL: 'danger', ERROR: 'danger',
  MANUAL_OBSERVATION: 'info',
  // 页面自造启停/观测态
  OBSERVE_ONLY: 'muted', ENABLED: 'success', OK: 'success',
}

const LIFECYCLE_LABEL: Record<string, string> = {
  PLANNED: '已登记', DEVELOPING: '开发中', MOCK_ONLY: '仅 Mock',
  PILOT: '试点', PRODUCTION: '正式', SUSPENDED: '暂停', RETIRED: '下线',
}

const RUNTIME_LABEL: Record<string, string> = {
  PLATFORM: '平台推送', EXTERNAL: '平台外推送', MANUAL: '人工上报',
}

const SOLE_PUSHER_LABEL: Record<string, string> = {
  CONFIRMED: '唯一推送方已确认', UNCONFIRMED: '唯一推送方待确认',
  CONFLICT: '唯一推送方冲突',
}

export function lifecycleLabel(v: string | null | undefined): string {
  if (!v) return '—'
  return LIFECYCLE_LABEL[v] ?? v
}
export function runtimeKindLabel(v: string | null | undefined): string {
  if (!v) return '—'
  return RUNTIME_LABEL[v] ?? v
}
export function solePusherLabel(v: string | null | undefined): string {
  if (!v) return '—'
  return SOLE_PUSHER_LABEL[v] ?? v
}
/** 未知状态保底可见（不静默转译）：有映射用中文，无映射原样返回。 */
export function bucketLabel(v: string | null | undefined): string {
  if (v === null || v === undefined || v === '') return '—'
  return BUCKET_LABEL[v] ?? v
}
export function bucketTone(v: string | null | undefined): string {
  return BUCKET_TONE[v ?? ''] ?? 'muted'
}
