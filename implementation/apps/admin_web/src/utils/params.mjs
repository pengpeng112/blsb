// LT-06B 告警规则参数表单（唯一事实源；params.ts 提供带类型 re-export）。
export const RULE_FORMS = [
  { kind: 'JOB_FAILED', label: '任务失败', fields: [
    { key: 'schedule_code', label: '调度编码', type: 'text', required: true },
  ] },
  { kind: 'RUN_TIMEOUT', label: '运行超时', fields: [] },
  { kind: 'RUN_STALLED', label: '运行停滞', fields: [] },
  { kind: 'SCHEDULE_SKIPPED', label: '班次连续跳过', fields: [
    { key: 'schedule_code', label: '调度编码', type: 'text', required: true },
    { key: 'consecutive', label: '连续次数', type: 'number', min: 1, max: 100,
      required: true },
  ] },
  { kind: 'NO_SUCCESS_WITHIN', label: '长时间无业务确认', fields: [
    { key: 'hours', label: '小时数', type: 'number', min: 1, max: 720,
      required: true },
    { key: 'accept_counts_as_activity', label: '受理也算活动（accept=true）',
      type: 'text', required: false,
      hint: '填 true 时前置机已受理（ACCEPTED）也算业务活动——传染病等"受理即成功"业务必填 true，否则会永久误报' },
  ] },
  { kind: 'BACKLOG_OVER', label: '积压超阈', fields: [
    { key: 'threshold', label: '阈值（条）', type: 'number', min: 1,
      required: true },
  ] },
  { kind: 'UNKNOWN_OVER', label: '未知超阈', fields: [
    { key: 'threshold', label: '阈值（条）', type: 'number', min: 1,
      required: true },
  ] },
  { kind: 'REJECT_RATE_OVER', label: '拒绝率超阈', fields: [
    { key: 'rate', label: '比例（0—1）', type: 'number', min: 0, max: 1,
      required: true },
    { key: 'min_total', label: '最小总数（不足不判）', type: 'number', min: 1,
      required: true },
  ] },
  { kind: 'DATE_EXPIRY', label: '日期到期', fields: [
    { key: 'source', label: '证据源', type: 'text',
      hint: '如 drug_trace.production_evidence（读实际登记有效期，不手填日期）' },
    { key: 'label', label: '名称', type: 'text', required: true },
    { key: 'warn_days', label: '提前告警（天）', type: 'number', min: 1,
      max: 365, required: true },
  ] },
  { kind: 'WATERMARK_STALE', label: '采集水位滞后', fields: [
    { key: 'table', label: '表名', type: 'text', required: true,
      hint: '如 ex_lab_item（传染病检验明细）' },
    { key: 'hours', label: '滞后小时数', type: 'number', min: 1, max: 720,
      required: true },
  ] },
  { kind: 'PARENT_WAIT_OVER', label: '待父受理积压', fields: [
    { key: 'threshold', label: '积压阈值（条）', type: 'number', min: 1,
      required: true },
  ] },
  { kind: 'WATCHDOG_STALE', label: '外部守护停摆', fields: [
    { key: 'status_path', label: '守护状态文件路径', type: 'text', required: false,
      hint: '如 <prod-path>/exchange/inf-watchdog/state/status.json（需挂载共享；留空=规则待核不误报）' },
    { key: 'minutes', label: '未刷新分钟数', type: 'number', min: 5, max: 1440,
      required: true },
  ] },
]

/** 表单值（字符串）→ params 对象（数值字段转 number；失败抛 Error 中文）。 */
export function formToParams(kind, values) {
  const def = RULE_FORMS.find(f => f.kind === kind)
  if (!def) throw new Error(`未知规则类型 ${kind}`)
  const out = {}
  for (const f of def.fields) {
    const raw = (values[f.key] ?? '').trim()
    if (!raw) {
      if (f.required) throw new Error(`${f.label} 必填`)
      continue
    }
    if (f.type === 'number') {
      const n = Number(raw)
      if (!Number.isFinite(n)) throw new Error(`${f.label} 须为数字`)
      if (f.min !== undefined && n < f.min) throw new Error(`${f.label} 不小于 ${f.min}`)
      if (f.max !== undefined && n > f.max) throw new Error(`${f.label} 不大于 ${f.max}`)
      out[f.key] = n
    } else {
      out[f.key] = raw
    }
  }
  return out
}

/** params 对象 → 表单值（编辑回填；数值转字符串）。 */
export function paramsToForm(kind, params) {
  const def = RULE_FORMS.find(f => f.kind === kind)
  const out = {}
  if (!def) return out
  for (const f of def.fields) {
    const v = params?.[f.key]
    out[f.key] = v === undefined || v === null ? '' : String(v)
  }
  return out
}
