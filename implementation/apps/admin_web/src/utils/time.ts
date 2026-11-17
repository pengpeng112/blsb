// LT-02 时间工具（纯 TS，无 Vue 依赖；node --test 直测）。
// 院内口径：固定 UTC+8（v0.4 §7.5），不依赖浏览器时区。

const HOSPITAL_OFFSET_MIN = 8 * 60

/** ISO 字符串/Date → 院内时间 "YYYY-MM-DD HH:mm:ss"；非法输入返回 "—"。
 RT-01：后端各接口的 naive isoformat() 时间（无 Z/+08:00 后缀，如
 "2026-11-11T02:00:00"）一律按 UTC 解释——与库列口径一致，否则 JS 会按
 浏览器本地时区解析导致显示随机器漂移。带时区后缀的值照常解析。 */
export function formatHospitalTime(iso: string | Date | null | undefined): string {
  if (iso === null || iso === undefined || iso === '') return '—'
  if (iso instanceof Date) {
    if (isNaN(iso.getTime())) return '—'
  } else {
    const s = String(iso)
    // 含日期+时间且无时区指示（末尾无 Z、无 ±HH:MM）→ 按 UTC 解析
    const hasTime = /[T ]\d{2}:\d{2}/.test(s)
    const hasTz = /(Z|[+-]\d{2}:?\d{2})$/.test(s)
    if (hasTime && !hasTz) {
      return formatHospitalTime(new Date(s.endsWith('Z') ? s : s + 'Z'))
    }
    if (isNaN(new Date(s).getTime())) return '—'
    return formatHospitalTime(new Date(s))
  }
  const t = new Date(iso.getTime() + HOSPITAL_OFFSET_MIN * 60_000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${t.getUTCFullYear()}-${p(t.getUTCMonth() + 1)}-${p(t.getUTCDate())} ` +
    `${p(t.getUTCHours())}:${p(t.getUTCMinutes())}:${p(t.getUTCSeconds())}`
}

/** 相对时间："刚刚"/"N 分钟前"/"N 小时前"/"N 天前"；超过 30 天显示院内日期。 */
export function relativeTime(iso: string | Date | null | undefined,
                             now: Date = new Date()): string {
  if (iso === null || iso === undefined || iso === '') return '—'
  const d = iso instanceof Date ? iso : new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const diffSec = Math.floor((now.getTime() - d.getTime()) / 1000)
  if (diffSec < 60) return '刚刚'
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前`
  if (diffSec < 30 * 86400) return `${Math.floor(diffSec / 86400)} 天前`
  return formatHospitalTime(d)
}

/** 最近 n 天的半开窗口 [now - n 天, now)，ISO 字符串（供 API 查询参数）。 */
export function daysAgoRange(n: number, now: Date = new Date()):
  { begin: string, end: string } {
  const endMs = now.getTime()
  const beginMs = endMs - n * 86400_000
  return { begin: new Date(beginMs).toISOString(), end: new Date(endMs).toISOString() }
}

/** RT-01：datetime-local 输入值（院内东八区本地时间 'YYYY-MM-DDTHH:mm'）
 → UTC 时间字符串 'YYYY-MM-DDTHH:mm:ss'。此前直接把本地字符串当 UTC 传后端，
 查询偏移 8 小时。
 输出刻意不带 'Z'/偏移后缀：后端 audit 接口用 datetime.fromisoformat 解析后
 直接与无时区 UTC 列比较，带时区的值在不同数据库上比较行为不一致。
 空串/非法输入返回 undefined（不产生查询参数）。 */
export function hospitalLocalInputToUtcIso(
  v: string | null | undefined): string | undefined {
  if (!v) return undefined
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(v.trim())
  if (!m) return undefined
  const d = new Date(`${m[1]}T${m[2]}:00+08:00`)
  if (isNaN(d.getTime())) return undefined
  return d.toISOString().slice(0, 19)
}
