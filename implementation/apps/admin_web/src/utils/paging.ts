// RT-03 S09 分页纯函数：类型化 re-export（逻辑唯一事实源在 paging.mjs，
// node:test 直接测 mjs；Vue/TS 层从这里 import 保持类型）。
import {
  durationLabel as _durationLabel,
  mergePagedItems as _mergePagedItems,
  pageStateReduce as _pageStateReduce,
  runStateHint as _runStateHint,
} from './paging.mjs'

/** 新页按 id 合并去重（实时列表：last_seen_at 变化可致跨页重复，见 S08）。 */
export function mergePagedItems<T>(
  existing: T[] | null | undefined,
  incoming: T[] | null | undefined, idKey = 'id'): T[] {
  return _mergePagedItems(existing, incoming, idKey) as T[]
}

export interface PageState<T> {
  items: T[]
  nextCursor: string | null
  loading: boolean
  loadingMore: boolean
  error: string
}

/** 加载更多状态机核心：reset/loaded/appended/failed（视图薄封装）。 */
export function pageStateReduce<T>(
  state: PageState<T>, action: string,
  response?: unknown, idKey = 'id'): PageState<T> {
  return _pageStateReduce(state, action, response, idKey) as PageState<T>
}

/** 耗时展示：null/undefined 显示 —（不把未知耗时显示为 0）。 */
export function durationLabel(seconds: number | null | undefined): string {
  return _durationLabel(seconds)
}

/** QUEUED/RUNNING/RETRY_WAIT 的口径说明（S09 要求清晰展示）。 */
export function runStateHint(state: string): string {
  return _runStateHint(state)
}
