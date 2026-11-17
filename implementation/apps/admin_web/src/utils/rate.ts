// LT-02 成功率计算（纯 TS）。
// 口径：CONFIRMED / (CONFIRMED + REJECTED + PARTIAL + UNKNOWN)；UNKNOWN 不计成功。
// 分母为 0 → 返回 null（调用方显示"—"），**绝不显示 100%**（任务书步骤4 红线）。
export interface Buckets { [k: string]: number }

export function successRate(buckets: Buckets | null | undefined): number | null {
  const b = buckets ?? {}
  const denom = (b.CONFIRMED ?? 0) + (b.REJECTED ?? 0) +
    (b.PARTIAL ?? 0) + (b.UNKNOWN ?? 0)
  if (denom <= 0) return null
  return (b.CONFIRMED ?? 0) / denom
}
