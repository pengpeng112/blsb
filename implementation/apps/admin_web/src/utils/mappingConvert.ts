// RT-05 mappingConvert 纯函数：类型化 re-export（唯一事实源 mappingConvert.mjs；node:test 直测 mjs）。

import { RULE_OPS as _RULE_OPS, parseRulesJson as _parseRulesJson,
  rulesToRows as _rulesToRows, rowsToRules as _rowsToRules,
  validateRows as _validateRows, normalizeDictRefs as _normalizeDictRefs,
} from './mappingConvert.mjs'

export interface OpParamSpec {
  key: string, type: 'string' | 'number' | 'string-list',
  required: boolean, label: string
}
export interface OpSpec { op: string, label: string, params: OpParamSpec[] }
export interface MappingRow {
  _idx: number, op: string, target: string,
  params: Record<string, unknown>, _extra: Record<string, unknown> | null
}

export const RULE_OPS = _RULE_OPS as OpSpec[]
export function parseRulesJson(text: string): {
  ok: boolean, value?: Record<string, unknown>, error?: string, advancedOnly?: boolean } {
  return _parseRulesJson(text)
}
export function rulesToRows(rules: unknown): {
  rows: MappingRow[] | null, error?: string, advancedOnly?: boolean } {
  return _rulesToRows(rules)
}
export function rowsToRules(rows: MappingRow[]): { ops: Record<string, unknown>[] } {
  return _rowsToRules(rows)
}
export function validateRows(rows: MappingRow[]): { row: number, message: string }[] {
  return _validateRows(rows)
}
export function normalizeDictRefs(refs: unknown): {
  ok: boolean, refs?: { namespace: string, version: unknown }[], error?: string } {
  return _normalizeDictRefs(refs)
}
