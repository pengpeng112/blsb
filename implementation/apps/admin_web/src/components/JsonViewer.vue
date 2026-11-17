<!-- 折叠 JSON 树：默认展开 2 层；复制按钮；超长字符串截断展开。
用法：<JsonViewer :data="detail" :default-depth="2"/> -->
<template>
  <div class="jv">
    <div class="jv-tools">
      <button class="ghost jv-copy" @click="copy">复制</button>
      <span v-if="copied" class="jv-copied">已复制</span>
    </div>
    <JvNode :value="data" :depth="0" :default-depth="defaultDepth" :path="root" />
  </div>
</template>
<script setup lang="ts">
import { h, defineComponent, ref, type PropType } from 'vue'

const props = withDefaults(defineProps<{
  data: unknown, defaultDepth?: number, root?: string
}>(), { defaultDepth: 2, root: '$' })

const copied = ref(false)
async function copy() {
  try {
    await navigator.clipboard.writeText(JSON.stringify(props.data, null, 2))
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch { /* 剪贴板不可用时静默（无敏感数据回退需求） */ }
}

const LONG = 160
const JvNode = defineComponent({
  name: 'JvNode',
  props: {
    value: {} as unknown as PropType<unknown>,
    depth: { type: Number, required: true },
    defaultDepth: { type: Number, required: true },
    path: { type: String, required: true },
    field: { type: String, default: '' },
  },
  setup(p) {
    const open = ref(p.depth < p.defaultDepth)
    const expandedStr = ref(false)
    const isObj = (v: unknown): v is Record<string, unknown> =>
      typeof v === 'object' && v !== null && !Array.isArray(v)
    const isArr = (v: unknown): v is unknown[] => Array.isArray(v)
    const render = (): any => {
      const v = p.value
      if (v === null) return h('span', { class: 'jv-null' }, 'null')
      if (isObj(v) || isArr(v)) {
        const entries = isObj(v) ? Object.entries(v) : v.map((x, i) => [String(i), x] as [string, unknown])
        const bracket = isObj(v) ? ['{', '}'] : ['[', ']']
        return h('div', { class: 'jv-node' }, [
          h('button', {
            class: 'jv-toggle ghost',
            onClick: () => { open.value = !open.value },
          }, `${open.value ? '▾' : '▸'} ${bracket[0]}${entries.length ? (open.value ? '' : `…${entries.length} 项`) : ''}${open.value ? '' : bracket[1]}`),
          ...(open.value ? [
            h('div', { class: 'jv-children' }, entries.map(([k, val]) =>
              h('div', { class: 'jv-row', key: k }, [
                h('span', { class: 'jv-key' }, isObj(v) ? `${k}:` : `[${k}]:`),
                h(JvNode, { value: val, depth: p.depth + 1,
                  defaultDepth: p.defaultDepth,
                  path: `${p.path}.${k}`, field: k }),
              ]))),
            h('span', { class: 'jv-close' }, bracket[1]),
          ] : []),
        ])
      }
      if (typeof v === 'string') {
        const long = v.length > LONG
        const shown = long && !expandedStr.value ? v.slice(0, LONG) + '…' : v
        return h('span', { class: 'jv-str' }, [
          `"${shown}"`,
          ...(long ? [h('button', {
            class: 'jv-more ghost',
            onClick: () => { expandedStr.value = !expandedStr.value },
          }, expandedStr.value ? '收起' : `展开(${v.length} 字)`)] : []),
        ])
      }
      return h('span', { class: `jv-${typeof v}` }, String(v))
    }
    return render
  },
})
</script>
<style scoped>
.jv { font-family: var(--font-mono); font-size: 12px; background: #0e1726; color: #c9e3ff; border-radius: var(--radius-lg); padding: 10px; overflow: auto; }
.jv-tools { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.jv-copy { background: none; border: 1px solid #2c4a70; color: #9fc3ef; border-radius: 4px; padding: 1px 8px; cursor: pointer; font-size: 12px; }
.jv-copied { color: #7ee2a8; font-size: 12px; }
.jv-node, .jv-row { margin: 1px 0; }
.jv-toggle { background: none; border: 0; color: #8fb8e8; cursor: pointer; padding: 0 4px 0 0; font-family: inherit; font-size: 12px; }
.jv-children { margin-left: 16px; border-left: 1px solid #1d3050; padding-left: 8px; }
.jv-key { color: #7fd1e8; margin-right: 4px; }
.jv-str { color: #a8e6a3; word-break: break-all; }
.jv-number { color: #f2c777; }
.jv-boolean { color: #e89bb5; }
.jv-null { color: #7c8aa0; }
.jv-more { background: none; border: 0; color: #7fd1e8; cursor: pointer; font-size: 11px; margin-left: 6px; padding: 0; }
.jv-close { color: #8fb8e8; }
</style>
