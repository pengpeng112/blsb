// RT-04 B3/B4 树形契约纯函数：类型化 re-export（唯一事实源 contractTree.mjs）。
import {
  flattenContractTree as _flattenContractTree,
  filterTreeRows as _filterTreeRows,
  splitEnvelopeInput as _splitEnvelopeInput,
} from './contractTree.mjs'

export interface ContractRow {
  path: string
  code: string
  name: string
  type: string
  max_len: number | null
  required: boolean
  depth: number
  array: boolean
}

export function flattenContractTree (
  contract: unknown, prefix?: string): ContractRow[] {
  return _flattenContractTree(contract, prefix)
}
export function filterTreeRows (rows: ContractRow[], query: string):
  ContractRow[] {
  return _filterTreeRows(rows, query)
}
export function splitEnvelopeInput (message: unknown): {
  envelope: Record<string, unknown>, input: unknown } {
  return _splitEnvelopeInput(message)
}
