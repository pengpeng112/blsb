// jsonDiff.mjs 类型声明（逻辑唯一事实源；UI 层可经此处获得类型）。
export interface DiffEntry {
  path: string
  kind: 'added' | 'removed' | 'changed' | 'type_changed'
  oldValue?: unknown
  newValue?: unknown
}
export function diffJson(a: unknown, b: unknown, path?: string): DiffEntry[]
export function summarizeDiff(entries: DiffEntry[]): {
  added: number, removed: number, changed: number, type_changed: number, total: number }
export function diffValueText(v: unknown): string
