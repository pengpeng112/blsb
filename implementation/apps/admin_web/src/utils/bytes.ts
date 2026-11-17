// LT-02 字节格式化（纯 TS）。v0.4 §7.5：原始字节数悬停展示由 BytesText 组件负责。
export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || isNaN(n)) return '—'
  if (n < 0) return '—'
  if (n < 1024) return `${Math.round(n)} B`
  const units = ['KB', 'MB', 'GB', 'TB', 'PB']
  let v = n
  let i = -1
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1 }
  return `${v.toFixed(1)} ${units[i]}`
}
