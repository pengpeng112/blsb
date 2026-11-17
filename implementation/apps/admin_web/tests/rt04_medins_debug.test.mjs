// RT-04 B4/B6：联调台升级与树契约纯函数（node:test；零新依赖）。
// 覆盖：树契约平面化（数组 items/嵌套/深度）、完整路径搜索、
// envelope/input 拆分、MedinsDebug 真实模板编译（历史行回填按钮角色门控、
// 加载更多保留、导出包分栏与元数据、契约树面板）。
import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import fs from 'node:fs'
import { parse, compileTemplate } from '@vue/compiler-sfc'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.dirname(HERE)
const V = (p) => path.join(ROOT, 'src', p)

import { flattenContractTree, filterTreeRows, splitEnvelopeInput }
  from '../src/utils/contractTree.mjs'

// ================================================= 纯函数
test('flattenContractTree：树契约 → 完整路径行（数组/嵌套/深度）', () => {
  const contract = { schema_version: 2, children: [
    { code: 'dscg_date', type: 'datetime', required: true },
    { code: 'diseinfo', type: 'array', required: true, items: {
      type: 'object', children: [
        { code: 'diag_code', type: 'string', required: true, max_len: 30 },
        { code: 'inpt', type: 'object', children: [
          { code: 'dept', type: 'string' }] } ] } },
    { code: 'tags', type: 'array', items: { type: 'string' } }] }
  const rows = flattenContractTree(contract)
  const byPath = Object.fromEntries(rows.map(r => [r.path, r]))
  assert.equal(byPath['input.diseinfo[]'].type, 'array')
  assert.equal(byPath['input.diseinfo[].diag_code'].required, true)
  assert.equal(byPath['input.diseinfo[].diag_code'].max_len, 30)
  assert.equal(byPath['input.diseinfo[].inpt.dept'].depth, 2)
  assert.ok('input.tags[][]' in byPath)            // 标量数组元素
})

test('flattenContractTree：平面契约（r1 nodes）兼容为顶层行', () => {
  const flat = { nodes: [{ code: 'patn_id', type: 'string' }] }
  const rows = flattenContractTree(flat)
  assert.equal(rows[0].path, 'input.patn_id')
  assert.equal(rows[0].depth, 0)
})

test('filterTreeRows：完整路径搜索（数组下标归一形）命中', () => {
  const rows = flattenContractTree({ children: [
    { code: 'diseinfo', type: 'array', items: { type: 'object', children: [
      { code: 'diag_code', type: 'string', name: '诊断代码' }] } }] })
  assert.equal(filterTreeRows(rows, 'diseinfo[].diag_code').length, 1)
  assert.equal(filterTreeRows(rows, '诊断代码').length, 1)
  assert.equal(filterTreeRows(rows, 'zzz').length, 0)
  assert.equal(filterTreeRows(rows, '').length, rows.length)
})

test('splitEnvelopeInput：公共报文头与业务 input 分离', () => {
  const msg = { info: '12601', msgid: 'M1', opter_name: 'X',
    input: { data: [{ list_id: 'L1' }] } }
  const { envelope, input } = splitEnvelopeInput(msg)
  assert.equal(envelope.info, '12601')
  assert.equal('input' in envelope, false)
  assert.deepEqual(input.data[0].list_id, 'L1')
})

// ================================================= 视图真实模板编译
// 视图 script 依赖 import.meta.env（http.ts），node 直载不可执行——
// 沿用 RT-03 模式：@vue/compiler-sfc parse+compileTemplate 全绿 + 模板
// 标记在位断言（B4：模板真实编译证明回填按钮/分栏/树面板语法有效）。
function compileView(rel) {
  const file = V(rel)
  const src = fs.readFileSync(file, 'utf8')
  const { descriptor, errors } = parse(src, { filename: file })
  assert.deepEqual(errors, [], `${rel} parse 失败`)
  const tpl = compileTemplate({
    source: descriptor.template.content, filename: file, id: 'x' })
  assert.deepEqual(tpl.errors, [], `${rel} template 编译失败`)
  return src
}

test('MedinsDebug 模板编译通过：黄条/加载更多（RT-03 不回退）/契约树面板', () => {
  const src = compileView('views/target/MedinsDebug.vue')
  assert.ok(src.includes('本页不会向医保平台发送任何请求'))
  assert.ok(src.includes('loadMoreHistory'))
  assert.ok(src.includes('mergePagedItems'))
  assert.ok(src.includes('historyCursor'))
  assert.ok(src.includes('字段契约（树形·完整路径搜索）'))
})

test('MedinsDebug：历史行回填按钮角色门控（canRecord=operator/admin）', () => {
  const src = compileView('views/target/MedinsDebug.vue')
  assert.ok(src.includes('v-if="canRecord"'))
  assert.ok(src.includes("roles.includes('operator')"))
  assert.ok(src.includes("roles.includes('platform_admin')"))
  assert.ok(src.includes('openRecord(r.id)'))       // 用目标 record_id 回填
})

test('MedinsDebug：回填失败保留表单 + 行刷新 outcome 如实', () => {
  const src = compileView('views/target/MedinsDebug.vue')
  assert.ok(src.includes('错误保留表单'))
  assert.ok(src.includes('refreshRowAfterRecord'))
  assert.ok(src.includes("success ? 'PASS' : 'FAIL'"))   // 失败回填反映 FAIL
})

test('MedinsDebug：导出包分栏（envelope/input）+ 元数据展示', () => {
  const src = compileView('views/target/MedinsDebug.vue')
  assert.ok(src.includes('公共报文头（envelope）'))
  assert.ok(src.includes('业务 input'))
  assert.ok(src.includes('exportPack.transaction_code'))
  assert.ok(src.includes('exportPack.profile_revision'))
  assert.ok(src.includes('JsonViewer'))
})
