// LT-07 医保联调台端点。
import { core } from './http'

/** 联调历史行（RT-03 S08 类型同步）。 */
export interface MedinsExchange {
  id: string
  code: string
  mode: string
  created_by: string
  created_at: string
  outcome: string
  validation_errors: unknown[]
  request_masked: Record<string, unknown> | null
  response_masked: Record<string, unknown> | null
  note: string
}

export function listMedinsTxns() {
  return core.get('/api/v1/medins-emr/transactions')
}
export function getMedinsTxn(code: string) {
  return core.get(`/api/v1/medins-emr/transactions/${code}`)
}
export function validateMedins(code: string, message: unknown):
  Promise<{ errors: any[], record_id: string, pass: boolean }> {
  return core.post('/api/v1/medins-emr/validate', { code, message })
}
export function sendMedinsMock(code: string, message: unknown,
  mockInfcode = 0) {
  return core.post('/api/v1/medins-emr/send-mock',
    { code, message, __mock_infcode: mockInfcode })
}
/** RT-04 B4：导出包（message 完整值仅本次响应回传；含交易码与 Profile revision）。 */
export interface MedinsExportPack {
  record_id: string
  transaction_code: string
  profile_revision?: { revision: string, digest?: string,
    schema_version?: number, transactions?: number }
  message: Record<string, unknown>
  headers: { name: string, value: string }[]
  curl: string
  notice: string
}
export function exportMedinsRequest(code: string, message: unknown):
  Promise<MedinsExportPack> {
  return core.post('/api/v1/medins-emr/export-request', { code, message })
}
/** RT-03 S08：联调历史分页对象（created_at DESC, id DESC keyset；
 *  next_cursor 为 null=末页；换交易筛选须重置游标）。 */
export function listMedinsExchanges(code?: string, cursor?: string,
  limit = 20): Promise<{ items: MedinsExchange[], next_cursor: string | null }> {
  const q: Record<string, string | number> = { limit }
  if (code) q.code = code
  if (cursor) q.cursor = cursor
  return core.get('/api/v1/medins-emr/debug-exchanges', q as any)
}
export function recordMedinsResult(id: string, body: {
  success: boolean, platform_code?: string, note: string }) {
  return core.post(
    `/api/v1/medins-emr/debug-exchanges/${id}/record-result`, body)
}
export const medinsApi = { listMedinsTxns, getMedinsTxn, validateMedins,
  sendMedinsMock, exportMedinsRequest, listMedinsExchanges,
  recordMedinsResult }
