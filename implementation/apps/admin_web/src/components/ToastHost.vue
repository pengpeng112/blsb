<!-- Toast 渲染宿主：App.vue 挂一次即可。
用法：App.vue <ToastHost/>；任意处 import { toast } ... toast.success('x') -->
<template>
  <Teleport to="body">
    <div class="toast-host" aria-live="polite">
      <div v-for="t in items" :key="t.id" class="toast" :class="`toast-${t.kind}`">
        <span>{{ t.msg }}</span>
        <button class="toast-x" @click="dismiss(t.id)" aria-label="关闭">✕</button>
      </div>
    </div>
  </Teleport>
</template>
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { subscribe, dismiss, type ToastItem } from './Toast'
const items = ref<ToastItem[]>([])
let un: (() => void) | null = null
onMounted(() => { un = subscribe(l => { items.value = l }) })
onUnmounted(() => { un?.() })
</script>
<style scoped>
.toast-host { position: fixed; top: 14px; right: 14px; z-index: 120; display: flex; flex-direction: column; gap: 8px; max-width: 380px; }
.toast { display: flex; align-items: center; gap: 10px; background: var(--c-surface); border: 1px solid var(--c-border); border-left-width: 4px; border-radius: var(--radius); padding: 8px 12px; box-shadow: var(--shadow-1); font-size: 13px; }
.toast-success { border-left-color: var(--c-success); }
.toast-info { border-left-color: var(--c-info); }
.toast-error { border-left-color: var(--c-danger); }
.toast-x { border: 0; background: none; cursor: pointer; color: var(--c-text-3); padding: 0 2px; }
</style>
