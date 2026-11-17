// mappingConvert.mjs 类型声明。
export interface OpParamSpec {
  key: string, type: 'string' | 'number' | 'string-list',
  required: boolean, label: string
}
export interface OpSpec { op: string, label: string, params: OpParamSpec[] }
export interface MappingRow {
  _idx: number, op: string, target: string,
  params: Record<string, unknown>, _extra: Record<string, unknown> | null
}
export const RULE_OPS: OpSpec[]
export function parseRulesJson(text: string): {
  ok: boolean, value?: Record<string, unknown>, error?: string, advancedOnly?: boolean }
export function rulesToRows(rules: unknown): {
  rows: MappingRow[] | null, error?: string, advancedOnly?: boolean }
export function rowsToRules(rows: MappingRow[]): { ops: Record<string, unknown>[] }
export function validateRows(rows: MappingRow[]): { row: number, message: string }[]
export function normalizeDictRefs(refs: unknown): {
  ok: boolean, refs?: { namespace: string, version: unknown }[], error?: string }
