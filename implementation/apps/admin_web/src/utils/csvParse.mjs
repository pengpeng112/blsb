// RT-05 U09/Metrics：RFC4180 语义 CSV 解析（纯函数；不用 split(',') 假解析）。
// 处理：UTF-8 BOM、CRLF/LF/CR、引号字段内逗号/换行/转义引号（""）、
// 空行跳过（计数）、列数与表头不一致的行（报行号）。
// 返回 {headers, rows, errors:[{line,message}], skippedEmptyLines}。

export function parseCsvText (text) {
  const errors = []
  let skipped = 0
  if (typeof text !== 'string') {
    return { headers: [], rows: [], errors: [{ line: 0, message: '输入须为文本' }], skippedEmptyLines: 0 }
  }
  let t = text
  if (t.charCodeAt(0) === 0xFEFF) t = t.slice(1)      // UTF-8 BOM
  // 逐字符状态机（引号字段内可含逗号/换行）
  const records = []
  let field = ''
  let record = []
  let inQuotes = false
  let line = 1
  let fieldStartLine = 1
  const pushField = () => { record.push(field); field = '' }
  const pushRecord = () => {
    pushField()
    if (record.length === 1 && record[0].trim() === '') {
      skipped++                          // 空行跳过（计数）
    } else {
      records.push({ rec: record, line: fieldStartLine })
    }
    record = []
  }
  for (let i = 0; i < t.length; i++) {
    const ch = t[i]
    if (inQuotes) {
      if (ch === '"') {
        if (t[i + 1] === '"') { field += '"'; i++ }   // 转义引号
        else inQuotes = false
      } else {
        if (ch === '\n') line++
        field += ch
      }
      continue
    }
    if (ch === '"') {
      if (field === '') { inQuotes = true; fieldStartLine = line }
      else field += ch                  // 引号外出现的引号按字面量（宽松）
      continue
    }
    if (ch === ',') { pushField(); fieldStartLine = line; continue }
    if (ch === '\r') {
      if (t[i + 1] === '\n') i++
      pushRecord()
      line++
      fieldStartLine = line
      continue
    }
    if (ch === '\n') { pushRecord(); line++; fieldStartLine = line; continue }
    field += ch
  }
  // 收尾：末尾无换行的最后一条；纯空输入不产记录
  if (field !== '' || record.length > 0) pushRecord()
  if (!records.length) {
    return { headers: [], rows: [], errors, skippedEmptyLines: skipped }
  }
  const headers = records[0].rec.map(h => h.trim())
  const rows = []
  for (let i = 1; i < records.length; i++) {
    const { rec, line: ln } = records[i]
    if (rec.length !== headers.length) {
      errors.push({ line: ln, message: `列数不匹配：应有 ${headers.length} 列，实际 ${rec.length} 列` })
      continue
    }
    rows.push(rec)
  }
  return { headers, rows, errors, skippedEmptyLines: skipped }
}
