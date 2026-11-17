// RT-06 F03 影像契约浏览端点（域路由通用挂载；登录+target_scope 后端校验）。
import { core } from './http'

export interface ImagingDatasetItem {
  code: string
  name: string
  field_count: number
  pending_count: number
  revision: string
}

export function listImagingDatasets(): Promise<
  { items: ImagingDatasetItem[], count: number, revision: string }> {
  return core.get('/api/v1/medins-imaging/datasets')
}

export function getImagingDataset(code: string): Promise<{
  code: string, revision: string, nodes: unknown[], sample: unknown }> {
  return core.get(`/api/v1/medins-imaging/datasets/${encodeURIComponent(code)}`)
}

export function validateImaging(dataset: string, records: unknown[]):
  Promise<{ dataset: string, ok_rows: number,
    rows: { row: number, ok: boolean, errors: unknown[] }[] }> {
  return core.post('/api/v1/medins-imaging/validate', { dataset, records })
}

export function projectImaging(dataset: string, records: unknown[]):
  Promise<{ submission_id: string, outcome: string, frozen: number,
    quality_waiting: unknown[], idempotent: boolean, mock: boolean }> {
  return core.post('/api/v1/medins-imaging/project', { dataset, records })
}
