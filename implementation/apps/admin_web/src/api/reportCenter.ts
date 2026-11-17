// LT-02 上报中心端点（LT-01 后端；后端未升级时调用会 404——页面显示 ErrorState，不伪造数据）。
import { core } from './http'
import type { ReportTarget, TargetSummary } from './types'

export function listTargets(): Promise<ReportTarget[]> {
  return core.get('/api/v1/report-targets')
}
export function getTarget(code: string): Promise<ReportTarget> {
  return core.get(`/api/v1/report-targets/${encodeURIComponent(code)}`)
}
export function createTarget(body: Partial<ReportTarget> & { code: string, name: string }) {
  return core.post('/api/v1/report-targets', body)
}
export function updateTarget(code: string, body: Partial<ReportTarget> & { revision: number }) {
  return core.put(`/api/v1/report-targets/${encodeURIComponent(code)}`, body)
}
export function targetSummary(code: string, days: number): Promise<TargetSummary> {
  return core.get(`/api/v1/report-targets/${encodeURIComponent(code)}/summary`,
    { days })
}
export function centerOverview(days: number):
  Promise<{ days: number, targets: TargetSummary[], totals: Record<string, number> }> {
  return core.get('/api/v1/report-center/overview', { days })
}

/** RT-05 U02：已注册领域插件只读元数据（新建业务下拉数据源；manifest 驱动）。 */
export interface PluginMeta {
  key: string
  domain: string
  description: string
  capabilities: string[]
  report_targets: string[]
}
export function listPlugins(): Promise<PluginMeta[]> {
  return core.get('/api/v1/plugins')
}
