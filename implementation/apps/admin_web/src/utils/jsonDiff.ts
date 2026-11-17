// RT-05 jsonDiff 纯函数：类型化 re-export（唯一事实源 jsonDiff.mjs；node:test 直测 mjs）。

import { diffJson as _diffJson, summarizeDiff as _summarizeDiff,
  diffValueText as _diffValueText } from './jsonDiff.mjs'

export interface DiffEntry {
  path: string
  kind: 'added' | 'removed' | 'changed' | 'type_changed'
  oldValue?: unknown
  newValue?: unknown
}

export function diffJson(a: unknown, b: unknown, path = '$'): DiffEntry[] {
  return _diffJson(a, b, path) as DiffEntry[]
}
export function summarizeDiff(entries: DiffEntry[]): {
  added: number, removed: number, changed: number, type_changed: number, total: number } {
  return _summarizeDiff(entries)
}
export function diffValueText(v: unknown): string {
  return _diffValueText(v)
}
