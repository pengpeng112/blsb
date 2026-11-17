// params.mjs 的类型声明。
export interface RuleFieldDef {
  key: string, label: string, type: 'number' | 'text' | 'date',
  min?: number, max?: number, required?: boolean, hint?: string }
export interface RuleFormDef { kind: string, label: string, fields: RuleFieldDef[] }
export const RULE_FORMS: RuleFormDef[]
export function formToParams(kind: string, values: Record<string, string>):
  Record<string, unknown>
export function paramsToForm(kind: string, params: Record<string, any>):
  Record<string, string>
