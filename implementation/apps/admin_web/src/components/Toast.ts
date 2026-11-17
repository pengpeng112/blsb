// LT-02 轻量 Toast：模块级订阅，无 Vue 依赖（ToastHost.vue 渲染）。
// 用法：import { toast } from '../components/Toast'; toast.success('已保存');
//      toast.error('失败（不自动消失）'); toast.info('提示');
export interface ToastItem {
  id: number
  kind: 'success' | 'error' | 'info'
  msg: string
}
type Listener = (items: ToastItem[]) => void

let items: ToastItem[] = []
let seq = 0
const listeners = new Set<Listener>()

function emit() { for (const l of listeners) l([...items]) }

function push(kind: ToastItem['kind'], msg: string) {
  const id = ++seq
  items = [...items, { id, kind, msg }]
  emit()
  if (kind !== 'error') setTimeout(() => dismiss(id), 4000)
  return id
}
export function dismiss(id: number) {
  items = items.filter(t => t.id !== id)
  emit()
}
export function subscribe(l: Listener): () => void {
  listeners.add(l)
  l([...items])
  return () => listeners.delete(l)
}
export const toast = {
  success: (m: string) => push('success', m),
  error: (m: string) => push('error', m),
  info: (m: string) => push('info', m),
}
