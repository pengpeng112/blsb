// csvParse.mjs 类型声明。
export interface CsvParseResult {
  headers: string[]
  rows: string[][]
  errors: { line: number, message: string }[]
  skippedEmptyLines: number
}
export function parseCsvText(text: string): CsvParseResult
