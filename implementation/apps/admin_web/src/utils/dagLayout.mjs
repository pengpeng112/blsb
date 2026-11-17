// RT-05 U07：DAG 布局（纯函数；纯 SVG 渲染的前置）。
// layoutDag(dsl) → {nodes:[{id,x,y,w,h,label,type,params}], edges:[{from,to,on}],
//                   layers:number, errors:string[]}
// 规则：
// - 长标签截断稳定宽度（节点盒固定 w/h，标签超长截断+title 全文）；
// - 拓扑分层（Kahn 分层；同一层内按 DSL 输入顺序排 x——坐标稳定）；
// - 环 → 明确错误（不死循环）；重复节点 id → 错误；悬空边（端点缺失）→
//   错误且该边不入图（不悄悄丢：errors 里报出）；
// - 孤立节点（无入边且无出边）正常布局并标记 isolated（可见，不丢）。

export const NODE_W = 132
export const NODE_H = 40
export const GAP_X = 56
export const GAP_Y = 28

/** @returns {{nodes:Array, edges:Array, layers:number, errors:string[]}} */
export function layoutDag (dsl) {
  const errors = []
  const nodesIn = Array.isArray(dsl?.nodes) ? dsl.nodes : []
  const edgesIn = Array.isArray(dsl?.edges) ? dsl.edges : []
  if (!nodesIn.length) {
    return { nodes: [], edges: [], layers: 0, errors: ['workflow has no nodes'] }
  }
  const seen = new Set()
  const byId = new Map()
  for (const n of nodesIn) {
    const id = String(n?.id ?? '')
    if (!id) { errors.push('node missing id'); continue }
    if (seen.has(id)) { errors.push(`duplicate node id ${id}`); continue }
    seen.add(id)
    byId.set(id, { id, label: String(n.label || n.id), type: String(n.type || '?') })
  }
  const edges = []
  const outDeg = new Map()
  const inDeg = new Map()
  for (const e of edgesIn) {
    const from = String(e?.from ?? '')
    const to = String(e?.to ?? '')
    const on = String(e?.on || 'success')
    if (!byId.has(from) || !byId.has(to)) {
      errors.push(`dangling edge ${from || '?'} → ${to || '?'}（端点不存在，边不入图）`)
      continue
    }
    edges.push({ from, to, on })
    outDeg.set(from, (outDeg.get(from) || 0) + 1)
    inDeg.set(to, (inDeg.get(to) || 0) + 1)
  }
  // Kahn 分层（带环检测：剩余节点无法进层 → cycle 错误，环节点不布局）
  const layer = new Map()
  const indegree = new Map()
  for (const id of byId.keys()) indegree.set(id, inDeg.get(id) || 0)
  let frontier = [...byId.keys()].filter(id => (indegree.get(id) || 0) === 0)
  let layerIdx = 0
  let placed = 0
  while (frontier.length) {
    const next = []
    for (const id of frontier) {
      layer.set(id, layerIdx)
      placed++
    }
    for (const id of frontier) {
      for (const e of edges) {
        if (e.from === id) {
          indegree.set(e.to, (indegree.get(e.to) || 1) - 1)
          if ((indegree.get(e.to) || 0) === 0) next.push(e.to)
        }
      }
    }
    frontier = next
    layerIdx++
  }
  if (placed < byId.size) {
    const stuck = [...byId.keys()].filter(id => !layer.has(id))
    errors.push(`存在环或不可达层：${stuck.join('、')}（节点不入图，须改正后再发布）`)
  }
  // 坐标：层内按输入顺序排 x（稳定）；孤立节点标记
  const layerMembers = new Map()
  for (const id of byId.keys()) {
    const L = layer.get(id)
    if (L === undefined) continue
    if (!layerMembers.has(L)) layerMembers.set(L, [])
    layerMembers.get(L).push(id)
  }
  const nodes = []
  for (const [L, ids] of [...layerMembers.entries()].sort((a, b) => a[0] - b[0])) {
    ids.forEach((id, i) => {
      const meta = byId.get(id)
      const isolated = !inDeg.get(id) && !outDeg.get(id)
      nodes.push({
        id, type: meta.type,
        label: meta.label.length > 14 ? meta.label.slice(0, 13) + '…' : meta.label,
        full: meta.label,
        x: 24 + i * (NODE_W + GAP_X),
        y: 24 + L * (NODE_H + GAP_Y),
        layer: L, isolated,
      })
    })
  }
  const pos = new Map(nodes.map(n => [n.id, n]))
  const drawnEdges = edges
    .filter(e => pos.has(e.from) && pos.has(e.to))
    .map(e => ({ ...e,
      x1: pos.get(e.from).x + NODE_W, y1: pos.get(e.from).y + NODE_H / 2,
      x2: pos.get(e.to).x, y2: pos.get(e.to).y + NODE_H / 2 }))
  return { nodes, edges: drawnEdges, layers: layerIdx, errors }
}

/** StepRun 状态 → 节点填充色（未知状态仍可见：灰色+问号描边样式）。 */
export function stateFill (state) {
  switch (state) {
    case 'SUCCEEDED': return { fill: '#dcfce7', stroke: '#16a34a' }
    case 'FAILED': return { fill: '#fee2e2', stroke: '#dc2626' }
    case 'NEEDS_REVIEW': case 'WAITING_HUMAN': return { fill: '#fef3c7', stroke: '#d97706' }
    case 'RUNNING': case 'READY': return { fill: '#dbeafe', stroke: '#2563eb' }
    case 'WAITING_EXTERNAL': return { fill: '#e0e7ff', stroke: '#4f46e5' }
    case 'SKIPPED': case 'CANCELLED': return { fill: '#f3f4f6', stroke: '#9ca3af' }
    case undefined: case null: case '': return { fill: '#ffffff', stroke: '#cbd5e1' }
    default: return { fill: '#faf5ff', stroke: '#a855f7' }   // 未知状态：可见+紫描边
  }
}
