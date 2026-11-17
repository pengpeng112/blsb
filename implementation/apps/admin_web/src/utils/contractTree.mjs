// RT-04 B3/B4：树形契约工具（node:test 可测的纯函数；TS 侧 re-export）。
// 契约形状（schema_version=2）：{ children: [{code,type,children?,items?}] }。

/** 树契约 → 平面行 [{path,code,name,type,max_len,required,depth,array}]。 */
export function flattenContractTree (contract, prefix = 'input') {
  const out = []
  const walk = (nodes, path, depth, inArray) => {
    for (const n of nodes || []) {
      const p = path ? `${path}.${n.code}` : n.code
      if (n.type === 'array' || n.items) {
        out.push({ path: `${p}[]`, code: n.code, name: n.name || '',
          type: 'array', max_len: n.max_len ?? null,
          required: !!n.required, depth, array: true })
        const itemPath = `${p}[]`
        const items = n.items || {}
        if (items.children) walk(items.children, itemPath, depth + 1, true)
        else if (items.type) {
          out.push({ path: `${itemPath}[]`, code: `${n.code}[]`,
            name: items.name || '', type: items.type,
            max_len: items.max_len ?? null, required: !!items.required,
            depth: depth + 1, array: true })
        }
      } else if (n.children) {
        out.push({ path: p, code: n.code, name: n.name || '',
          type: 'object', max_len: n.max_len ?? null,
          required: !!n.required, depth, array: inArray })
        walk(n.children, p, depth + 1, inArray)
      } else {
        out.push({ path: p, code: n.code, name: n.name || '',
          type: n.type || 'string', max_len: n.max_len ?? null,
          required: !!n.required, depth, array: inArray })
      }
    }
  }
  walk(contract?.children || contract?.nodes || [], prefix, 0, false)
  return out
}

/** 完整路径搜索（大小写不敏感；同时命中 code/name）。 */
export function filterTreeRows (rows, query) {
  const q = String(query || '').trim().toLowerCase()
  if (!q) return rows
  return rows.filter(r =>
    r.path.toLowerCase().includes(q) ||
    String(r.name || '').toLowerCase().includes(q))
}

/** 报文拆分：公共报文头（除 input 外根字段）与业务 input（B4 分开展示）。 */
export function splitEnvelopeInput (message) {
  const env = {}
  let input = null
  for (const [k, v] of Object.entries(message || {})) {
    if (k === 'input') input = v
    else env[k] = v
  }
  return { envelope: env, input }
}
