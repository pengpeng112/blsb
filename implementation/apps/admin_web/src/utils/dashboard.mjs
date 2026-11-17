// LT-06B 驾驶舱纯逻辑（唯一事实源；node:test 直接 import；
// dashboard.ts 对外提供带类型的 re-export，Vue 层照常 import .ts）。
export const LIFECYCLE_ORDER = {
  PRODUCTION: 0, PILOT: 1, MOCK_ONLY: 2, DEVELOPING: 3, PLANNED: 4,
  SUSPENDED: 5, RETIRED: 6,
}

/** RT-01：统一取业务编码——后端 TargetSummary 返回 target_code（无 code），
 ReportTarget 目录返回 code。两种字段都兼容，杜绝 undefined.localeCompare
 整页白屏（默认种子两个 PRODUCTION 即触发）。 */
export function targetCodeOf(t) {
  return t.code ?? t.target_code ?? ''
}

/** lifecycle 排序（PRODUCTION→PLANNED；未知排最后，次键 code 稳定序）。 */
export function sortTargets(targets) {
  return [...targets].sort((a, b) => {
    const la = LIFECYCLE_ORDER[a.lifecycle ?? ''] ?? 99
    const lb = LIFECYCLE_ORDER[b.lifecycle ?? ''] ?? 99
    if (la !== lb) return la - lb
    const ca = String(a.code ?? a.target_code ?? '')
    const cb = String(b.code ?? b.target_code ?? '')
    return ca.localeCompare(cb)
  })
}

/** 待办条（最多 5 条）：CRITICAL 告警 > 未知积压/无确认 > 到期类 > 双上报冲突。 */
export function buildTodos(alerts, summaries, targets) {
  const out = []
  const nameOf = (code) => targets.find(
    t => (t.code ?? t.target_code) === code)?.name ?? code
  for (const a of alerts) {
    if (a.state === 'RESOLVED') continue
    if (String(a.severity).toUpperCase() === 'CRITICAL') {
      out.push({ key: `alert-${a.id}`, tone: 'danger',
        text: `[CRITICAL] ${a.message}`, to: '/alerts' })
    }
  }
  for (const s of summaries) {
    const unknown = Number(s.buckets?.UNKNOWN ?? 0)
    if (unknown > 0) {
      out.push({ key: `unknown-${s.target_code}`, tone: 'warning',
        text: `${nameOf(s.target_code)} 存在 ${unknown} 条未知结果（UNKNOWN）`,
        to: `/ledger?target=${encodeURIComponent(s.target_code)}&bucket=UNKNOWN` })
    }
    // PT-03：前置机受理型业务（CONFIRMED=0 但 ACCEPTED>0）已有明确受理证据，
    // 不再刷"尚无业务确认"假警告——只有连受理证据都没有才提示
    const accepted = Number(s.buckets?.ACCEPTED ?? 0)
    if (s.last_success_at === null && accepted === 0
        && s.data_source !== 'none') {
      out.push({ key: `nosuccess-${s.target_code}`, tone: 'warning',
        text: `${nameOf(s.target_code)} 尚无任何受理或确认记录` })
    }
  }
  for (const a of alerts) {
    if (a.state !== 'RESOLVED' && a.alert_type === 'DATE_EXPIRY') {
      out.push({ key: `expiry-${a.id}`, tone: 'warning', text: a.message,
        to: '/alerts' })
    }
  }
  for (const t of targets) {
    if (t.sole_pusher === 'CONFLICT') {
      const tc = t.code ?? t.target_code ?? ''
      out.push({ key: `conflict-${tc}`, tone: 'danger',
        text: `${t.name} 存在双上报风险（唯一推送方冲突，待协调）`,
        to: `/targets/${encodeURIComponent(tc)}` })
    }
  }
  const seen = new Set()
  const uniq = out.filter(t => (seen.has(t.key) ? false : (seen.add(t.key), true)))
  uniq.sort((a, b) => (a.tone === b.tone ? 0 : a.tone === 'danger' ? -1 : 1))
  return uniq.slice(0, 5)
}

/** 合并多业务 daily 为堆叠序列：[{date, CONFIRMED, ACCEPTED, REJECTED, UNKNOWN}]
 * （PT-03：补绘"已受理"序列——前置机受理型业务不再贴地）。 */
export function stackDaily(summaries) {
  const byDate = {}
  for (const s of summaries) {
    for (const row of s.daily ?? []) {
      const d = String(row.date)
      byDate[d] = byDate[d] ?? {}
      for (const k of ['CONFIRMED', 'ACCEPTED', 'REJECTED', 'UNKNOWN']) {
        byDate[d][k] = (byDate[d][k] ?? 0) + Number(row[k] ?? 0)
      }
    }
  }
  return Object.keys(byDate).sort().map(date => ({ date, ...byDate[date] }))
}

/** PT-03：主成功指标口径——CONFIRMED>0 用"已确认"；否则 ACCEPTED>0 用
 * "已受理"（前置机受理型业务，如传染病 13 接口）；两者皆 0 显示 0。
 * 返回 {value, label, badge}；受理≠国家平台终审，badge 如实标注口径。 */
export function primarySuccessMetric(s) {
  const b = s?.buckets ?? {}
  const confirmed = Number(b.CONFIRMED ?? 0)
  const accepted = Number(b.ACCEPTED ?? 0)
  if (confirmed > 0 || accepted === 0) {
    return { value: confirmed, label: '已确认', badge: '' }
  }
  return { value: accepted, label: '已受理', badge: '前置机受理' }
}

/** 调度行文案：镜像（仅观测，不提供启动）/停用/下次触发（北京时间）。 */
export function describeSchedule(s, nowIso) {
  if (s.observe_only) {
    return { label: '镜像·仅观测（由外部定时执行，平台未接管）', tone: 'muted' }
  }
  if (!s.enabled) {
    return { label: '已停用', tone: 'muted' }
  }
  if (!s.next_fire_at) {
    return { label: '启用（待计算下次触发）', tone: 'warn' }
  }
  const t = new Date(s.next_fire_at + (s.next_fire_at.endsWith('Z') ? '' : 'Z'))
  if (Number.isNaN(t.getTime())) {
    return { label: `下次 ${s.next_fire_at}`, tone: 'ok' }
  }
  const diffMin = Math.round(
    (t.getTime() - Date.parse(nowIso ?? new Date().toISOString())) / 60000)
  const inCst = new Date(t.getTime() + 8 * 3600_000).toISOString().slice(11, 16)
  if (diffMin < 0) {
    return { label: `下次 ${inCst}（北京时间，已到期待触发）`, tone: 'warn' }
  }
  return { label: `下次 ${inCst}（北京时间，约 ${diffMin} 分钟后）`, tone: 'ok' }
}
