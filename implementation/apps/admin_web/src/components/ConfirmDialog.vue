<!-- 危险操作确认：requireReason 时必须输入 ≥5 字理由才可确认。
用法：模板里 <ConfirmDialog v-model:open="d" title="更正(新版本)" require-text
  :require-reason="true" confirm-text="确认更正" v-model:reason="why" @confirm="submit"/>
或命令式：const ok = await confirmDanger(...) 见 Toast/组合式；本组件保持模板式。 -->
<template>
  <Modal :open="open" :title="title" @update:open="v => $emit('update:open', v)">
    <p class="cd-impact"><slot /></p>
    <label v-if="requireReason" class="cd-reason">
      处理理由（≥5 字，将传给后端并记审计）
      <textarea v-model="reasonModel" rows="3" maxlength="200"
        :placeholder="'例如：源系统补录后按新版本重新上报'"></textarea>
    </label>
    <p v-if="reasonError" class="cd-err" role="alert">{{ reasonError }}</p>
    <div class="cd-row">
      <button class="danger" :disabled="busy || !canConfirm" @click="onConfirm">
        {{ confirmText }}</button>
      <button class="ghost" @click="$emit('update:open', false)" :disabled="busy">取消</button>
    </div>
  </Modal>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Modal from './Modal.vue'
const props = withDefaults(defineProps<{
  open: boolean, title?: string, confirmText?: string,
  requireReason?: boolean, busy?: boolean,
  reason?: string
}>(), { title: '确认操作', confirmText: '确认' })
const emit = defineEmits<{ (e: 'update:open', v: boolean): void,
  (e: 'confirm', reason: string): void, (e: 'update:reason', v: string): void }>()
const reasonModel = computed({
  get: () => props.reason ?? '',
  set: (v: string) => emit('update:reason', v),
})
const reasonError = ref('')
const canConfirm = computed(() =>
  !props.requireReason || reasonModel.value.trim().length >= 5)
watch(() => props.open, v => { if (v) reasonError.value = '' })
function onConfirm() {
  if (props.requireReason && reasonModel.value.trim().length < 5) {
    reasonError.value = '请填写至少 5 个字符的处理理由'
    return
  }
  emit('confirm', reasonModel.value.trim())
}
</script>
<style scoped>
.cd-impact { color: var(--c-text-2); margin: 0 0 12px; }
.cd-reason { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.cd-err { color: var(--c-danger); font-size: 13px; }
.cd-row { display: flex; gap: 8px; margin-top: 14px; }
button.danger { background: var(--c-danger); }
</style>
