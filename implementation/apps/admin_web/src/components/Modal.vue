<!-- 居中弹窗：v-model:open；Esc 关闭。
用法：<Modal v-model:open="show" title="修改密码"><form @submit.prevent="save">…</form></Modal>
RT-01 关键修复：关闭（✕/遮罩/Esc）同时触发 update:open(false) 与 close——
此前只发 update:open，而大量页面绑定 @close，弹窗一旦打开就关不掉（P0）。 -->
<template>
  <Teleport to="body">
    <div v-if="open" class="mo-mask" @click="close">
      <div class="mo-panel" :style="{ width }" role="dialog" aria-modal="true" @click.stop>
        <div class="mo-head">
          <h3>{{ title }}</h3>
          <button class="ghost mo-close" @click="close" aria-label="关闭">✕</button>
        </div>
        <div class="mo-body"><slot /></div>
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
<style scoped>
.mo-mask { position: fixed; inset: 0; z-index: 90; background: #0006; display: grid; place-items: center; }
.mo-panel { background: var(--c-surface); border-radius: var(--radius-lg); width: 420px; max-width: 92vw; max-height: 88vh; display: flex; flex-direction: column; box-shadow: var(--shadow-1); }
.mo-head { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--c-border); }
.mo-head h3 { margin: 0; font-size: 15px; }
.mo-close { border: 0; background: none; cursor: pointer; color: var(--c-text-2); }
.mo-body { padding: 16px; overflow: auto; }
</style>
