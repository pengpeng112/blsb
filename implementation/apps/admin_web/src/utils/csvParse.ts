// RT-05 csvParse 纯函数：类型化 re-export（唯一事实源 csvParse.mjs；node:test 直测 mjs）。

import { parseCsvText as _parseCsvText } from './csvParse.mjs'

export interface CsvParseResult {
  headers: string[]
  rows: string[][]
  errors: { line: number, message: string }[]
  skippedEmptyLines: number
}

export function parseCsvText(text: string): CsvParseResult {
  return _parseCsvText(text)
}
