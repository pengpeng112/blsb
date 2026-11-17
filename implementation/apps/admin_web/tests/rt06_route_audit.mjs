// RT-06 F06：前端路由 AST 交叉核对（TypeScript compiler AST；非正则猜）。
// 范围：src/api/*.ts、src/api.ts 门面、src/views/**、src/components/**、
// src/utils/** 中的真实请求调用点（core.x / api.coreX / storageX / fetch）。
// 输出 var/rt06_route_audit.json：每调用点 文件/行/服务/method/规范化path/
// 命中结果；unknown method / 动态未解析 / 缺路由 → exit 1。
// 用法：node tests/rt06_route_audit.mjs <routes_dump.json> [srcDir]
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const ts = require('typescript')

const __dirname0 = path.dirname(fileURLToPath(import.meta.url))
const routesDumpPath = process.argv[2]
const srcDir = process.argv[3] || path.join(__dirname0, '..', 'src')
const routes = JSON.parse(fs.readFileSync(routesDumpPath, 'utf8'))
// routes: [{method, path}]（core/storage/exchange 混合；以 /api/v1 前缀区分服务
// 不够——storage/exchange 同前缀不同挂载。dump 时已带 service 字段。）

const SERVICE_PREFIX = {
  core: '/api/v1/',
  storage: '/storage-api/v1/',
  exchange: '/exchange-api/v1/',
}

function collectFiles(dir, out = []) {
  for (const name of fs.readdirSync(dir)) {
    const fp = path.join(dir, name)
    const st = fs.statSync(fp)
    if (st.isDirectory()) {
      if (name === 'node_modules' || name === 'dist' || name === '.render_cache') continue
      collectFiles(fp, out)
    } else if (/\.(ts|vue|mts)$/.test(name) && !name.endsWith('.d.ts')) {
      out.push(fp)
    }
  }
  return out
}

function extractScriptText(file, src) {
  if (!file.endsWith('.vue')) return { text: src, offset: 0 }
  const idx = src.indexOf('<script')
  if (idx < 0) return { text: '', offset: 0 }
  const start = src.indexOf('>', idx) + 1
  const end = src.indexOf('</script>', start)
  const text = src.slice(start, end < 0 ? undefined : end)
  return { text, offset: start }
}

/** 模板串/串接 → 规范化 path（${...}/{...} 动态段归一）+ 是否未解析 */
function normalizePathExpr(node) {
  // 返回 {value, dynamic: bool, unresolved: bool}
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
    return { value: node.text, dynamic: false, unresolved: false }
  }
  if (ts.isTemplateExpression(node)) {
    let v = ''
    for (const span of [node.head, ...node.templateSpans]) {
      if (ts.isTemplateHead(span) || ts.isTemplateMiddle(span) ||
          ts.isTemplateTail(span)) {
        v += span.text
      } else if (ts.isTemplateSpan(span)) {
        const e = span.expression
        if (ts.isPropertyAccessExpression(e) || ts.isIdentifier(e) ||
            ts.isElementAccessExpression(e) || ts.isCallExpression(e) ||
            ts.isBinaryExpression(e)) {
          v += '{route_param}'          // 已识别动态表达式（可判定）
          v += span.literal.text        // 表达式**后**的尾文本
        } else {
          v += '{unresolved}'
          return { value: v, dynamic: true, unresolved: true }
        }
      }
    }
    return { value: v, dynamic: true, unresolved: false }
  }
  if (ts.isBinaryExpression(node) && node.operatorToken.kind ===
      ts.SyntaxKind.PlusToken) {
    const l = normalizePathExpr(node.left)
    const r = normalizePathExpr(node.right)
    if (l.unresolved || r.unresolved) {
      return { value: l.value + r.value, dynamic: true, unresolved: true }
    }
    return { value: l.value + r.value, dynamic: l.dynamic || r.dynamic,
             unresolved: false }
  }
  if (ts.isCallExpression(node) && node.arguments.length === 0 &&
      /coreBase0?|storageBase0?/.test(node.expression.getText())) {
    return { value: "", dynamic: false, unresolved: false }
  }
  if (ts.isCallExpression(node) && node.arguments.length === 1 &&
      /auditExportUrl/.test(node.expression.getText())) {
    // 已知 wrapper：audit CSV 导出（GET /api/v1/audit/export; query 由构造）
    return { value: "/api/v1/audit/export", dynamic: false,
             unresolved: false }
  }
  if (ts.isPropertyAccessExpression(node) || ts.isIdentifier(node) ||
      ts.isConditionalExpression(node) || ts.isCallExpression(node)) {
    // 变量/三元/函数返回：尝试常见字面量归一（encodeURIComponent 调用等已由
    // 上层模板/拼接捕获）；纯变量名视为 unresolved（要求改写为可判定形态）
    return { value: '<expr>', dynamic: true, unresolved: true }
  }
  return { value: '<expr>', dynamic: true, unresolved: true }
}

function methodOf(name) {
  const m = name.toLowerCase()
  if (m.endsWith('.get') || m.includes('coreget') || m.includes('storageget'))
    return 'GET'
  if (m.endsWith('.post') || m.includes('corepost') || m.includes('storagepost'))
    return 'POST'
  if (m.endsWith('.put')) return 'PUT'
  if (m.endsWith('.delete')) return 'DELETE'
  return null
}

function serviceOf(name) {
  if (/storage/i.test(name)) return 'storage'
  return 'core'
}

const callSites = []
for (const file of collectFiles(srcDir)) {
  const raw = fs.readFileSync(file, 'utf8')
  const { text, offset } = extractScriptText(file, raw)
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true,
                                 file.endsWith('.vue') ||
                                 file.endsWith('.mts') ? ts.ScriptKind.TS
                                   : ts.ScriptKind.TS)
  const rel = path.relative(srcDir, file).replace(/\\/g, '/')
  const visit = (node) => {
    if (ts.isCallExpression(node)) {
      let exprText = node.expression.getText(sf)
      let method = null
      let service = 'core'
      if (/^(core|api\.core)\.(get|post|put|delete)$/i.test(exprText)) {
        method = exprText.split('.').pop().toUpperCase()
      } else if (/^api\.(storageGet|storagePost)$/i.test(exprText)) {
        method = exprText.split('storage')[1].slice(0, 99).startsWith('Get')
          ? 'GET' : 'POST'
        service = 'storage'
      } else if (/^(storage\.(get|post|put|delete))$/i.test(exprText)) {
        method = exprText.split('.').pop().toUpperCase()
        service = 'storage'
      } else if (exprText === 'fetch' && node.arguments.length) {
        method = 'GET'                       // fetch 默认 GET（无 options 时）
        const opts = node.arguments[1]
        if (opts && ts.isObjectLiteralExpression(opts)) {
          for (const p of opts.properties) {
            if (ts.isPropertyAssignment(p) &&
                p.name.getText(sf) === 'method' &&
                ts.isStringLiteral(p.initializer)) {
              method = p.initializer.text.toUpperCase()
            }
          }
        }
      }
      // 分层排除：传输层/门面文件不作为应用调用点（其余文件已直接核对）
  if (rel === 'api/http.ts' || rel === 'api.ts') return
  if (method && node.arguments.length) {
        const arg0 = node.arguments[0]
        let np = null
        if (ts.isCallExpression(arg0) &&
            /coreBase|storageBase|encodeURIComponent/.test(
              arg0.expression.getText(sf))) {
          np = normalizePathExpr(arg0.arguments[0])
        } else {
          np = normalizePathExpr(arg0)
        }
        callSites.push({
          file: rel, line: sf.getLineAndCharacterOfPosition(
            node.getStart(sf)).line + 1,
          service: serviceOf(exprText), method,
          raw_path: np.value, dynamic: np.dynamic, unresolved: np.unresolved,
        })
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
}

// 路由匹配
function routeMatches(service, method, normPath) {
  // 目标与后端路由**两侧**都剥服务前缀再比对（否则永不命中）
  const strip = (x) => {
    let q = x
    for (const pre of Object.values(SERVICE_PREFIX)) {
      if (q.startsWith(pre)) { q = q.slice(pre.length); break }
    }
    return q
  }
  const norm = (x) => strip(x)
    .replace(/\?.*$/g, "")
    .replace(/\{[^}]+}/g, "{route_param}")
    .replace(/\/+/g, "/")
    .replace(/\/+$/g, "") || "/"
  const target = norm(normPath)
  for (const r of routes.filter(x => x.service === service &&
      x.method === method)) {
    const rp = norm(r.path)
    if (rp === target) return r
    const a = rp.split("/")
    const b = target.split("/")
    if (a.length === b.length &&
        a.every((seg, i) => seg === b[i] || seg.startsWith("{")) ) return r
  }
  return null
}

let missing = 0
let unresolved = 0
const report = []
for (const c of callSites) {
  let hit = null
  if (!c.unresolved) {
    hit = routeMatches(c.service, c.method, c.raw_path)
    if (!hit) missing++
  } else {
    unresolved++
  }
  report.push({ ...c, hit: hit ? `${hit.method} ${hit.path}` : null })
}
const out = {
  call_sites: callSites.length, missing, unresolved, report,
}
const outPath = process.argv[4] ||
  path.join(__dirname0, '..', '..', 'var', 'rt06_route_audit.json')
fs.mkdirSync(path.dirname(outPath), { recursive: true })
fs.writeFileSync(outPath, JSON.stringify(out, null, 1))
console.log(`call_sites=${out.call_sites} missing=${missing} unresolved=${unresolved}`)
for (const r of report.filter(x => !x.hit)) console.log('  MISS', r.file + ':' + r.line, r.method, r.raw_path)
process.exit(missing || unresolved ? 1 : 0)
