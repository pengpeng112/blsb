// LT-06B 告警规则参数表单：类型化 re-export（逻辑唯一事实源在 params.mjs）。
import {
  formToParams as _formToParams, paramsToForm as _paramsToForm,
  RULE_FORMS as _RULE_FORMS,
} from './params.mjs'

export interface RuleFieldDef {
  key: string
  label: string
  type: 'number' | 'text' | 'date'
  min?: number
  max?: number
  required?: boolean
  hint?: string
}
export interface RuleFormDef {
  kind: string
  label: string
  fields: RuleFieldDef[]
}

export const RULE_FORMS: RuleFormDef[] = _RULE_FORMS

export function formToParams(kind: string,
  values: Record<string, string>): Record<string, unknown> {
  return _formToParams(kind, values)
}

export function paramsToForm(kind: string,
  params: Record<string, any>): Record<string, string> {
  return _paramsToForm(kind, params)
}
