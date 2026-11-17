// RT-01 零新依赖 Vue SFC 真实渲染加载器（node --test 直跑）。
// 思路：@vue/compiler-sfc（parse + compileScript + compileTemplate）把 .vue
// 编译为组件 JS，esbuild（Vite 自带）完成 TS→ESM/相对依赖打包；vue 与
// vue/server-renderer 由 Node 从 node_modules 直接加载（全部组件共享同一
// runtime 实例），createSSRApp + renderToString 输出真实 HTML 供断言。
// 编译产物缓存在 tests/.render_cache/（.gitignore 已忽略）。
import { pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import esbuild from 'esbuild'

const HERE = path.dirname(fileURLToPath(import.meta.url))          // tests/
const ROOT = path.dirname(HERE)                                    // admin_web/
const CACHE_DIR = path.join(HERE, '.render_cache')
// RT-05 U10：缓存命名空间——入口 hash 只含入口文件内容，依赖组件变化会
// 复用旧包（假通过）。测试进程可在导入本模块前设置 process.env.
// SFC_CACHE_NS=rt05v1 获得本轮专属命名空间（不改名不删其他任务缓存）；
// 缺省保持 rt01v1 兼容既有测试。
const CACHE_NS = process.env.SFC_CACHE_NS || 'rt01v1'

function ensureCache() {
  fs.mkdirSync(CACHE_DIR, { recursive: true })
  const gi = path.join(HERE, '.gitignore')
  if (!fs.existsSync(gi)) fs.writeFileSync(gi, '.render_cache/\n')
}

// ------------------------------------------------------------ vue-router 桩
// Tabs 等组件在 setup 里调 useRoute/useRouter；渲染测试不装真路由，桩返回
// 稳定空值（点击行为不在 SSR 断言范围）。
function routerStubPath() {
  ensureCache()
  const p = path.join(CACHE_DIR, 'vue-router.stub.mjs')
  if (!fs.existsSync(p)) {
    fs.writeFileSync(p, `// 测试桩：SSR 渲染用
export function useRoute() { return { path: '/', query: {}, params: {}, meta: {} } }
export function useRouter() { return { push() {}, replace() {}, back() {} } }
export const RouterLink = { name: 'RouterLink', props: { to: {} }, render: () => null }
export const RouterView = { name: 'RouterView', render: () => null }
`)
  }
  return p
}

// ------------------------------------------------------------ SFC → JS
function compileVueSource(src, filename, id) {
  const { descriptor, errors } = parse(src, { filename })
  if (errors.length) {
    throw new Error(`SFC parse 失败 ${filename}: ` +
      errors.map(e => e.message ?? String(e)).join('; '))
  }
  let scriptCode = ''
  let bindings
  if (descriptor.scriptSetup || descriptor.script) {
    const s = compileScript(descriptor, { id, genDefaultAs: '_sfc_main' })
    scriptCode = s.content
    bindings = s.bindings
  } else {
    scriptCode = 'const _sfc_main = {}'
  }
  if (!descriptor.template || !descriptor.template.content) {
    return scriptCode + '\nexport default _sfc_main\n'
  }
  const tpl = compileTemplate({
    id, filename, source: descriptor.template.content,
    compilerOptions: { bindingMetadata: bindings },
  })
  if (tpl.errors && tpl.errors.length) {
    throw new Error(`模板编译失败 ${filename}: ` +
      tpl.errors.map(e => (e && e.message) ?? String(e)).join('; '))
  }
  return scriptCode + '\n' +
    tpl.code.replace('export function render', 'function render') +
    '\n_sfc_main.render = render\nexport default _sfc_main\n'
}

function sfcPlugin() {
  return {
    name: 'rt01-sfc',
    setup(build) {
      build.onLoad({ filter: /\.vue$/ }, async args => {
        const src = fs.readFileSync(args.path, 'utf8')
        const id = createHash('sha1').update(args.path).digest('hex').slice(0, 8)
        // compileScript 产物含 TS 注解——必须声明 loader: 'ts' 让 esbuild 转译
        return { contents: compileVueSource(src, args.path, id), loader: 'ts' }
      })
      build.onLoad({ filter: /\.ts$/ }, async args => {
        const src = fs.readFileSync(args.path, 'utf8')
        const r = await esbuild.transform(src, { loader: 'ts', format: 'esm' })
        return { contents: r.code, loader: 'js' }
      })
    },
  }
}

const loaded = new Map()

/** 加载一个 .vue 组件模块（default export = 组件选项对象；真实编译+打包）。 */
export function loadSfc(absPath) {
  ensureCache()
  const key = path.resolve(absPath)
  if (!loaded.has(key)) {
    loaded.set(key, (async () => {
      const src = fs.readFileSync(key, 'utf8')
      const hash = createHash('sha1')
        .update(src).update(CACHE_NS).digest('hex').slice(0, 12)
      const outfile = path.join(CACHE_DIR,
        path.basename(key).replace(/\.vue$/, '') + '-' + hash + '.mjs')
      if (!fs.existsSync(outfile)) {
        const result = await esbuild.build({
          stdin: {
            contents: `export { default } from ${JSON.stringify(key)}`,
            resolveDir: path.dirname(key),
            loader: 'js',
          },
          outfile, bundle: true, format: 'esm', platform: 'node',
          // 只把 'vue' 交还 Node 解析（全部模块共享同一 runtime 实例）；
          // vue-router 由下方插件桩替换；其余相对依赖在本地打包。
          external: ['vue'],
          // RT-05 U10：视图 script 依赖 import.meta.env（http.ts 顶层取默认后端）。
          // 注入静态 env 字面量后视图可被 node 真实加载并 SSR（onMounted 不执行、
          // 不发请求）；仅测试加载器行为，不改业务代码。
          define: {
            'import.meta.env': JSON.stringify({
              VITE_CORE_DEFAULT: 'http://127.0.0.1:8000',
              VITE_STORAGE_DEFAULT: 'http://127.0.0.1:8001',
              DEV: false,
              MODE: 'test', PROD: false, SSR: true,
            }),
          },
          plugins: [
            sfcPlugin(),
            { name: 'rt01-router-stub',
              setup(b) {
                b.onResolve({ filter: /^vue-router$/ },
                  () => ({ path: routerStubPath() }))
              } },
          ],
          logLevel: 'silent',
        })
        if (result.errors.length) {
          throw new Error('SFC bundle 失败 ' + key + ': ' +
            result.errors.map(e => e.text).join('; '))
        }
      }
      return import(pathToFileURL(outfile).href)
    })())
  }
  return loaded.get(key)
}

// ------------------------------------------------------------ 纯 TS 链加载
// routes.ts / payloads.ts 等不做 bundle（其中 dynamic import 指向 .vue，
// bundle 会把全部视图拉进来）；逐文件 transform 后把相对导入改写为缓存目录
// 内编译产物的相对引用（缓存目录内自洽）。
const tsCache = new Map()

/** 同步把依赖文件编译到缓存目录，返回 "./xxx.mjs" 形式的相对 specifier。 */
function depFileUrlSync(target) {
  // TS 惯例允许省略扩展名（'./menu' → menu.ts）；依次探测候选
  const candidates = [target, `${target}.ts`, `${target}.mts`,
    `${target}.mjs`, `${target}.js`, path.join(target, 'index.ts')]
  const found = candidates.find(p => fs.existsSync(p) && fs.statSync(p).isFile())
  if (!found) {
    throw new Error('loadTs 依赖解析失败（无候选文件）: ' + target)
  }
  const src = fs.readFileSync(found, 'utf8')
  const hash = createHash('sha1').update(src).update(CACHE_NS).digest('hex').slice(0, 12)
  const base = path.basename(found)
  const outfile = path.join(CACHE_DIR,
    base.replace(/\.(ts|mts|mjs|js)$/, '') + '-' + hash + '.mjs')
  if (!fs.existsSync(outfile)) {
    if (found.endsWith('.vue')) {
      // 纯 TS 链不应依赖 .vue；防御性报错而不是静默失败
      throw new Error('纯 TS 模块链不支持 import .vue：' + found)
    }
    const r = esbuild.transformSync(src, {
      loader: found.endsWith('.ts') ? 'ts' : 'js', format: 'esm' })
    const dir = path.dirname(found)
    const code = r.code.replace(
      /(from\s*|import\s*\(\s*)(["'])(\.\.?\/[^"']+)\2/g,
      (m, head, q, spec) => {
        // .vue 动态导入（routes.ts 的 VIEW_MAP 懒加载）保持原样：仅构造
        // 路由表时存在、运行时才解析，本链路从不调用
        if (spec.endsWith('.vue')) return m
        return `${head}${q}${depFileUrlSync(path.resolve(dir, spec))}${q}`
      })
    fs.writeFileSync(outfile, code)
  }
  return './' + path.basename(outfile)
}

/** 加载一个纯 TS/JS 模块（路由表/载荷构造/状态映射等）。 */
export function loadTs(absPath) {
  ensureCache()
  const key = path.resolve(absPath)
  if (!tsCache.has(key)) {
    tsCache.set(key, (async () => {
      const src = fs.readFileSync(key, 'utf8')
      const r = await esbuild.transform(src, {
        loader: key.endsWith('.mjs') ? 'js' : 'ts', format: 'esm' })
      const hash = createHash('sha1').update(src).update(CACHE_NS).digest('hex').slice(0, 12)
      const outfile = path.join(CACHE_DIR,
        path.basename(key).replace(/\.(ts|mjs)$/, '') + '-' + hash + '.mjs')
      if (!fs.existsSync(outfile)) {
        const dir = path.dirname(key)
        const code = r.code.replace(
          /(from\s*|import\s*\(\s*)(["'])(\.\.?\/[^"']+)\2/g,
          (m, head, q, spec) => {
            if (spec.endsWith('.vue')) return m   // 懒加载视图保持原样（不解析）
            return `${head}${q}${depFileUrlSync(path.resolve(dir, spec))}${q}`
          })
        fs.writeFileSync(outfile, code)
      }
      return import(pathToFileURL(outfile).href)
    })())
  }
  return tsCache.get(key)
}
// ---------------------------------------------------------- SSR 渲染辅助
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'

/** 以真实 SSR 渲染一个组件（default export），返回 HTML 字符串。 */
export async function renderComponent(comp, opts = {}) {
  const app = createSSRApp({
    render: () => h(comp, opts.props ?? {}, opts.slots ?? {}),
  })
  return renderToString(app)
}

/** 渲染一个内联测试组件（setup 返回渲染函数，避免依赖运行时模板编译）。 */
export async function renderInline(setupFn) {
  const app = createSSRApp({ setup: setupFn })
  return renderToString(app)
}
