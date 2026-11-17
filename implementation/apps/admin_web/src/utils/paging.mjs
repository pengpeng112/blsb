// RT-03 S09 分页纯函数（mjs 唯一事实源；node:test 直接测；paging.ts re-export）。
// 约定（S08/S09）：列表分页是实时口径——alerts 的 last_seen_at 会变化，
// 前端按 ID 合并去重；刷新/改筛选重置游标；加载更多失败保留已加载内容。

/** 把新页合并进已加载列表：按 id 去重（保留首次出现位置，实时列表下
 *  重复行只留一条），返回新数组（不修改输入）。 */
export function mergePagedItems(existing, incoming, idKey = 'id') {
  const seen = new Set((existing ?? []).map((r) => String(r[idKey])))
  const out = (existing ?? []).slice()
  for (const row of incoming ?? []) {
    const k = String(row[idKey])
    if (seen.has(k)) continue
    seen.add(k)
    out.push(row)
  }
  return out
}

/** 加载更多状态机的纯函数核心（供视图与测试共用）：
 *  - reset：清空 items/游标/错误（改筛选时调用，防旧游标串页）
 *  - append：成功翻页——items 合并去重、游标取响应 next_cursor
 *  - fail：保留已加载内容与当前游标，只记错误（可重试）
 *  state 形如 { items, nextCursor, loading, loadingMore, error }；返回新对象。 */
export function pageStateReduce(state, action, response, idKey = 'id') {
  switch (action) {
    case 'reset':
      return { ...state, items: [], nextCursor: null, error: '' }
    case 'loading':
      return { ...state, loading: true, error: '' }
    case 'loadingMore':
      return { ...state, loadingMore: true, error: '' }
    case 'loaded': {
      const items = mergePagedItems([], response?.items ?? [], idKey)
      return { ...state, loading: false, loadingMore: false,
        items,
        nextCursor: response?.next_cursor ?? null }
    }
    case 'appended': {
      const items = mergePagedItems(state.items, response?.items ?? [], idKey)
      return { ...state, loading: false, loadingMore: false, items,
        nextCursor: response?.next_cursor ?? null }
    }
    case 'failed':
      return { ...state, loading: false, loadingMore: false,
        error: String(response?.message ?? response ?? '加载失败') }
    default:
      return state
  }
}

/** 耗时展示（S09：null 显示"—"，不把 null 显示为 0）；QUEUED/RUNNING/
 *  RETRY_WAIT 状态说明独立于耗时。 */
export function durationLabel(seconds) {
  if (seconds == null) return '—'
  return `${seconds}s`
}

export function runStateHint(state) {
  switch (state) {
    case 'QUEUED': return '排队中（尚未开跑，耗时未知）'
    case 'RUNNING': return '执行中（首次开跑起计时）'
    case 'RETRY_WAIT': return '等待重试（含在总耗时内）'
    default: return ''
  }
}
