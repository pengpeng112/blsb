<template>
  <div class="login">
    <div class="box">
      <h2>医院数据归集与上报平台</h2>
      <p v-if="isDev" class="muted">本地开发令牌：dev-admin-token / dev-reviewer-token / dev-operator-token</p>
      <input v-model="t" placeholder="访问令牌" @keyup.enter="go" style="width:100%" />
      <details class="advanced">
        <summary class="muted">后端地址（打包版默认连不上时修改）</summary>
        <label>Core API<input v-model="core" placeholder="http://127.0.0.1:8000" @keyup.enter="go" /></label>
        <label>Storage API<input v-model="storage" placeholder="http://127.0.0.1:8001" @keyup.enter="go" /></label>
        <p class="muted" style="font-size:12px">保存到本浏览器（blyb.core / blyb.storage），登录时生效。</p>
      </details>
      <p v-if="err" class="err">{{ err }}</p>
      <button @click="go" :disabled="busy">{{ busy ? '验证中…' : '登录' }}</button>
    </div>
  </div>
</template>
<script setup lang="ts">
import { ref } from 'vue'
import { login, savedBackends, setBackends } from '../api'
const e = defineEmits<{ (x: 'done', name: string): void }>()
const saved = savedBackends()
// 生产构建不预填演示令牌（dev-* 已禁用会 401）；同源发行默认地址由构建期注入。
const isDev = !!import.meta.env.DEV
const t = ref(isDev ? 'dev-admin-token' : ''), err = ref(''), busy = ref(false)
const core = ref(saved.core), storage = ref(saved.storage)
async function go() {
  busy.value = true; err.value = ''
  try {
    setBackends(core.value, storage.value)
    const s = await login(t.value); e('done', s.display_name || s.code)
  }
  catch (x: any) { err.value = x.message || '登录失败' }
  finally { busy.value = false }
}
</script>
<style scoped>
.login { display: grid; place-items: center; height: 100vh; }
.box { width: 340px; background: #fff; border-radius: 10px; padding: 26px; display: flex; flex-direction: column; gap: 12px; }
.err { color: #b23a3a; font-size: 12px; }
.advanced { font-size: 13px; }
.advanced label { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; font-size: 12px; color: #666; }
.advanced input { width: 100%; }
</style>
