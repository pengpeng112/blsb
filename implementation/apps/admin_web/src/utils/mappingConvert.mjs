// RT-05 U05：映射规则 JSON ↔ 表格无损互转（纯函数）。
// 真实算子白名单（与 packages/mapping_engine ALLOWED_OPS 逐字一致；
// 旧计划的 coalesce 不是当前算子，禁止 UI 发明）。
// 无损策略：
// - 每行携带行内未知键 `_extra`（未知扩展键保留）；
// - 表格不支持的 op（未知算子/非对象/缺 ops 数组）→ 整份文档标
//   advancedOnly，UI 只读提示切高级模式，禁止重组丢配置；
// - JSON→表格→JSON 往返保留字段、类型、顺序、空值。

export const RULE_OPS = [
  { op: 'const', label: '常量', params: [
    { key: 'value', type: 'string', required: true, label: '常量值' }] },
  { op: 'field', label: '取源字段', params: [
    { key: 'source_field', type: 'string', required: true, label: '源字段' }] },
  { op: 'dict_map', label: '字典映射', params: [
    { key: 'source_field', type: 'string', required: true, label: '源字段' },
    { key: 'dictionary', type: 'string', required: true, label: '字典名' },
    { key: 'default', type: 'string', required: false, label: '缺省值' }] },
  { op: 'concat', label: '拼接', params: [
    { key: 'parts', type: 'string-list', required: true, label: '片段列表(逗号分隔)' }] },
  { op: 'date_format', label: '日期格式', params: [
    { key: 'source_field', type: 'string', required: true, label: '源字段' },
    { key: 'from_format', type: 'string', required: true, label: '源格式' },
    { key: 'to_format', type: 'string', required: true, label: '目标格式' }] },
  { op: 'decimal_scale', label: '小数位', params: [
    { key: 'source_field', type: 'string', required: true, label: '源字段' },
    { key: 'places', type: 'number', required: true, label: '小数位' }] },
  { op: 'regex_replace', label: '正则替换', params: [
    { key: 'source_field', type: 'string', required: true, label: '源字段' },
    { key: 'pattern', type: 'string', required: true, label: '正则' },
    { key: 'replacement', type: 'string', required: true, label: '替换为' }] },
  { op: 'null_fallback', label: '空值回退', params: [
    { key: 'source_field', type: 'string', required: true, label: '源字段' },
    { key: 'fallback_field', type: 'string', required: true, label: '回退字段' }] },
]

const KNOWN_OPS = new Set(RULE_OPS.map(o => o.op))

/** 解析并校验 rules JSON 文本 → {ok, value?, error?, advancedOnly}。 */
export function parseRulesJson (text) {
  let v
  try {
    v = JSON.parse(text)
  } catch (e) {
    return { ok: false, error: `JSON 解析失败：${e.message}` }
  }
  if (v === null || typeof v !== 'object' || Array.isArray(v)) {
    return { ok: false, error: 'rules 须为 JSON 对象（含 ops 数组）' }
  }
  const ops = v.ops
  if (!Array.isArray(ops)) {
    return { ok: false, error: 'rules.ops 须为数组' }
  }
  const advanced = !ops.every(o => o && typeof o === 'object'
    && !Array.isArray(o) && KNOWN_OPS.has(String(o.op || '')))
  return { ok: true, value: v, advancedOnly: advanced }
}

/** rules → 表格行（无损：未知键入 _extra；advancedOnly 时返回 null rows）。 */
export function rulesToRows (rules) {
  const parsed = parseRulesJson(JSON.stringify(rules))
  if (!parsed.ok) return { rows: null, error: parsed.error }
  if (parsed.advancedOnly) return { rows: null, advancedOnly: true }
  const rows = (rules.ops || []).map((o, idx) => {
    const known = {}
    const extra = {}
    for (const [k, v] of Object.entries(o)) {
      if (k === 'op' || k === 'target') { known[k] = v; continue }
      const spec = RULE_OPS.find(x => x.op === o.op)
      if (spec && spec.params.some(p => p.key === k)) known[k] = v
      else extra[k] = v
    }
    return { _idx: idx, op: String(o.op || ''), target: String(o.target ?? ''),
      params: known, _extra: Object.keys(extra).length ? extra : null }
  })
  return { rows }
}

/** 表格行 → rules（无损：_extra 并回；保持行序）。 */
export function rowsToRules (rows) {
  const ops = (rows || []).map(r => {
    const spec = RULE_OPS.find(x => x.op === r.op)
    const o = { target: r.target ?? '', op: r.op }
    for (const p of (spec ? spec.params : [])) {
      const v = (r.params || {})[p.key]
      if (p.type === 'string-list') {
        o[p.key] = Array.isArray(v) ? v
          : String(v ?? '').split(',').map(x => x.trim()).filter(x => x !== '')
      } else if (p.type === 'number') {
        o[p.key] = v === '' || v === null || v === undefined ? v : Number(v)
      } else {
        o[p.key] = v ?? ''
      }
    }
    if (r._extra && typeof r._extra === 'object') Object.assign(o, r._extra)
    return o
  })
  return { ops }
}

/** 行级提交前校验（必填/数字/危险 regex）→ [{row, message}]；空=通过。 */
export function validateRows (rows) {
  const errs = []
  ;(rows || []).forEach((r, i) => {
    const spec = RULE_OPS.find(x => x.op === r.op)
    if (!spec) { errs.push({ row: i, message: `未知算子 ${r.op}（白名单外，服务端必拒）` }); return }
    if (!String(r.target ?? '').trim()) errs.push({ row: i, message: 'target 必填' })
    for (const p of spec.params) {
      const v = (r.params || {})[p.key]
      if (p.required && (v === undefined || v === null ||
          (typeof v === 'string' && !v.trim()))) {
        errs.push({ row: i, message: `${spec.label}.${p.label} 必填` })
      }
      if (p.type === 'number' && v !== '' && v !== null && v !== undefined
          && !Number.isFinite(Number(v))) {
        errs.push({ row: i, message: `${spec.label}.${p.label} 须为数字` })
      }
      if (p.key === 'pattern' && typeof v === 'string' && v) {
        if (v.length > 128) errs.push({ row: i, message: '正则长度超 128（服务端上限）' })
        if (/\(\?[^=<!]/.test(v) || /(\.\*){2,}/.test(v) || /\*[^)]\*/.test(v)) {
          errs.push({ row: i, message: '疑似灾难性回退正则（服务端会拒绝）' })
        }
      }
    }
  })
  return errs
}

/** 字典引用规范化：[{namespace, version}]；"最新"不合法——必须显式版本。 */
export function normalizeDictRefs (refs) {
  const out = []
  for (const r of refs || []) {
    const ns = String(r.namespace ?? r.dictionary ?? '').trim()
    const v = r.version
    if (!ns) continue
    if (v === undefined || v === null || String(v).trim() === '' ||
        String(v) === 'latest') {
      return { ok: false, error: `字典 ${ns} 未提供显式 version（禁止 latest 漂移引用）` }
    }
    out.push({ namespace: ns, version: v })
  }
  return { ok: true, refs: out }
}
