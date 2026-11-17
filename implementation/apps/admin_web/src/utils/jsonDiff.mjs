// RT-05 U06：JSON 深度差异（纯函数；键顺序变化不制造伪 diff）。
// diffJson(a, b, path?) → [{path, kind, oldValue?, newValue?}]
//   kind ∈ added | removed | changed | type_changed
// 规则：对象逐键集合比较（键序无关）；数组按索引逐项；标量用 === 严格
// 比较（类型变化显式标注 type_changed）；undefined 与缺失键等价（不产噪）。

/** @returns {Array<{path:string, kind:string, oldValue?:any, newValue?:any}>} */
export function diffJson (a, b, path = '$') {
  const out = []
  if (a === b) return out
  const ta = kindOf(a)
  const tb = kindOf(b)
  if (ta !== tb) {
    // null/undefined 与缺失等价：一侧缺失按 added/removed
    if (a === undefined) { out.push({ path, kind: 'added', newValue: b }); return out }
    if (b === undefined) { out.push({ path, kind: 'removed', oldValue: a }); return out }
    out.push({ path, kind: 'type_changed', oldValue: a, newValue: b })
    return out
  }
  if (ta === 'object') {
    const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})])
    for (const k of Array.from(keys).sort()) {
      const p = path === '$' ? k : `${path}.${k}`
      const av = a ? a[k] : undefined
      const bv = b ? b[k] : undefined
      if (av === undefined && bv === undefined) continue
      if (av === undefined) { out.push({ path: p, kind: 'added', newValue: bv }); continue }
      if (bv === undefined) { out.push({ path: p, kind: 'removed', oldValue: av }); continue }
      out.push(...diffJson(av, bv, p))
    }
    return out
  }
  if (ta === 'array') {
    const n = Math.max((a || []).length, (b || []).length)
    for (let i = 0; i < n; i++) {
      const p = `${path}[${i}]`
      const av = (a || [])[i]
      const bv = (b || [])[i]
      if (av === undefined) { out.push({ path: p, kind: 'added', newValue: bv }); continue }
      if (bv === undefined) { out.push({ path: p, kind: 'removed', oldValue: av }); continue }
      out.push(...diffJson(av, bv, p))
    }
    return out
  }
  if (a !== b) {
    // 类型变化显式标注（任务书 U06：类型变化可见）；同类型值差异=changed
    const kind = typeof a !== typeof b ? 'type_changed' : 'changed'
    out.push({ path, kind, oldValue: a, newValue: b })
  }
  return out
}

function kindOf (v) {
  if (v === null) return 'null'
  if (Array.isArray(v)) return 'array'
  return typeof v
}

/** 差异摘要：{added, removed, changed, type_changed, total}。 */
export function summarizeDiff (entries) {
  const s = { added: 0, removed: 0, changed: 0, type_changed: 0, total: 0 }
  for (const e of entries || []) {
    if (e.kind in s) s[e.kind]++
    s.total++
  }
  return s
}

/** 显示值的安全渲染：对象/数组折叠为简短 JSON 摘要（完整值由 JsonViewer 承担）。 */
export function diffValueText (v) {
  if (v === undefined) return '（无）'
  if (v === null) return 'null'
  if (typeof v === 'string') return v.length > 80 ? v.slice(0, 80) + '…' : v
  if (typeof v === 'object') {
    const keys = Object.keys(v)
    const t = Array.isArray(v) ? `数组(${v.length})` : `对象{${keys.slice(0, 4).join(',')}${keys.length > 4 ? '…' : ''}}`
    return t
  }
  return String(v)
}
