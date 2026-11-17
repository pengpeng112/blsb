<template>
  <div class="login">
    <div class="box">
      <h2>医院数据归集与上报平台</h2>
      <form @submit.prevent="go" class="credentials">
        <label>用户名<input v-model="username" name="username" autocomplete="username" required maxlength="64" autofocus :disabled="busy" /></label>
        <label>密码<input v-model="password" name="password" type="password" autocomplete="current-password" required maxlength="128" :disabled="busy" /></label>
        <p v-if="err" class="err" role="alert">{{ err }}</p>
        <button type="submit" :disabled="busy">{{ busy ? '登录中…' : '登录' }}</button>
      </form>
      <details class="advanced">
        <summary class="muted">后端地址（打包版默认连不上时修改）</summary>
        <label>Core API<input v-model="core" placeholder="http://127.0.0.1:8000" @keyup.enter="go" /></label>
        <label>Storage API<input v-model="storage" placeholder="http://127.0.0.1:8001" @keyup.enter="go" /></label>
        <p class="muted" style="font-size:12px">保存到本浏览器（blyb.core / blyb.storage），登录时生效。</p>
      </details>
      <details v-if="isDev" class="advanced">
        <summary class="muted">本地开发工具</summary>
        <input v-model="devToken" placeholder="开发令牌" />
        <button @click="devLogin" :disabled="busy">开发登录</button>
      </details>
    </div>
  </div>
</template>
<script setup lang="ts">
import { ref } from 'vue'
import { login, loginWithToken, savedBackends, setBackends } from '../api'
const e = defineEmits<{ (x: 'done', name: string): void }>()
const saved = savedBackends()
// 生产构建不预填演示令牌（dev-* 已禁用会 401）；同源发行默认地址由构建期注入。
const isDev = !!import.meta.env.DEV
const username = ref(''), password = ref(''), err = ref(''), busy = ref(false)
const devToken = ref(isDev ? 'dev-admin-token' : '')
const core = ref(saved.core), storage = ref(saved.storage)
async function go() {
  busy.value = true; err.value = ''
  try {
    setBackends(core.value, storage.value)
    const s = await login(username.value.trim(), password.value)
    password.value = ''
    e('done', s.display_name || s.code)
  }
  catch (x: any) { err.value = x.message || '登录失败' }
  finally { busy.value = false }
}
async function devLogin() {
  busy.value = true; err.value = ''
  try {
    setBackends(core.value, storage.value)
    const s = await loginWithToken(devToken.value); e('done', s.display_name || s.code)
  } catch (x: any) { err.value = x.message || '登录失败' }
  finally { busy.value = false }
}
</script>
<style scoped>
.login { display: grid; place-items: center; height: 100vh; }
.box { width: 340px; background: #fff; border-radius: 10px; padding: 26px; display: flex; flex-direction: column; gap: 12px; }
.credentials, .credentials label { display: flex; flex-direction: column; gap: 8px; }
.credentials { gap: 14px; }
.err { color: #b23a3a; font-size: 12px; }
.advanced { font-size: 13px; }
.advanced label { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; font-size: 12px; color: #666; }
.advanced input { width: 100%; }
</style>
