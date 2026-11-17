// paging.mjs 的类型声明（逻辑唯一事实源；TS 由 paging.ts re-export）。
export function mergePagedItems<T>(
  existing: T[] | null | undefined, incoming: T[] | null | undefined,
  idKey?: string): T[]
export interface PageState<T> {
  items: T[]
  nextCursor: string | null
  loading: boolean
  loadingMore: boolean
  error: string
}
export function pageStateReduce<T>(
  state: PageState<T>, action: string, response?: unknown,
  idKey?: string): PageState<T>
export function durationLabel(seconds: number | null | undefined): string
export function runStateHint(state: string): string
