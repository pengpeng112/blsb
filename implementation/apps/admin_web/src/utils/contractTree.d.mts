// contractTree.mjs 的类型声明（逻辑唯一事实源；TS 由 contractTree.ts re-export）。
export interface ContractRowRaw {
  path: string
  code: string
  name: string
  type: string
  max_len: number | null
  required: boolean
  depth: number
  array: boolean
}
export function flattenContractTree(
  contract: unknown, prefix?: string): ContractRowRaw[]
export function filterTreeRows(
  rows: ContractRowRaw[], query: string): ContractRowRaw[]
export function splitEnvelopeInput(message: unknown): {
  envelope: Record<string, unknown>, input: unknown }
