<!-- 右侧抽屉：v-model:open；Esc 关闭。
用法：<Drawer v-model:open="open" title="详情" width="520px"><JsonViewer :data="obj"/></Drawer>
RT-01 关键修复：关闭（✕/遮罩/Esc）同时触发 update:open(false) 与 close——
此前只发 update:open，而大量页面绑定 @close，弹窗一旦打开就关不掉（P0）。 -->
<template>
  <Teleport to="body">
    <div v-if="open" class="dr-mask" @click="close">
      <div class="dr-panel" :style="{ width }" @click.stop>
        <div class="dr-head">
          <h3>{{ title }}</h3>
          <button class="ghost dr-close" @click="close" aria-label="关闭">✕</button>
        </div>
        <div class="dr-body"><slot /></div>
      </div>
    </div>
  </Teleport>
</template>
<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
const props = defineProps<{ open: boolean, title?: string, width?: string }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void,
  (e: 'close'): void }>()
function close() {
  emit('update:open', false)
  emit('close')
}
function onKey(e: KeyboardEvent) { if (e.key === 'Escape' && props.open) close() }
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>
<script lang="ts">
export default { inheritAttrs: false }
</script>
<style scoped>
.dr-mask { position: fixed; inset: 0; z-index: 80; background: #0006; display: flex; justify-content: flex-end; }
.dr-panel { background: var(--c-surface); height: 100%; max-width: 92vw; box-shadow: -4px 0 16px #0002; display: flex; flex-direction: column; animation: dr-in .18s ease-out; }
@keyframes dr-in { from { transform: translateX(30px); opacity: .4; } to { transform: none; opacity: 1; } }
.dr-head { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--c-border); }
.dr-head h3 { margin: 0; font-size: 15px; }
.dr-close { border: 0; background: none; font-size: 14px; cursor: pointer; color: var(--c-text-2); }
.dr-body { padding: 14px 16px; overflow: auto; flex: 1; }
</style>
