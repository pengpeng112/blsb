<template>
  <AppLayout v-if="loggedIn" :menu="MENU" :user="who" :roles="roles"
    :scopes="scopes" :password-session="passwordSession"
    @logout="logout" @change-password="showPassword = true">
    <router-view />
  </AppLayout>
  <div v-else class="login-wrap">
    <Login @done="onLogin" />
  </div>
  <ToastHost />
  <Modal :open="showPassword" title="修改密码" @update:open="showPassword = $event">
    <form v-if="showPassword" @submit.prevent="savePassword">
      <label class="pw-label">原密码<input v-model="oldPassword" type="password" autocomplete="current-password" required maxlength="128" /></label>
      <label class="pw-label">新密码<input v-model="newPassword" type="password" autocomplete="new-password" required minlength="12" maxlength="128" /></label>
      <label class="pw-label">确认新密码<input v-model="repeatPassword" type="password" autocomplete="new-password" required minlength="12" maxlength="128" /></label>
      <p class="muted">密码须为12至128个字符，修改后请重新登录。</p>
      <p v-if="passwordError" class="err" role="alert">{{ passwordError }}</p>
      <div class="row"><button :disabled="passwordBusy">保存</button>
        <button type="button" class="ghost" @click="showPassword = false" :disabled="passwordBusy">取消</button></div>
    </form>
  </Modal>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import AppLayout from './components/AppLayout.vue'
import ToastHost from './components/ToastHost.vue'
import Modal from './components/Modal.vue'
import { MENU } from './router/menu'
import Login from './views/Login.vue'
import { currentUser, token, clearLogin, logout as apiLogout, changePassword, persistIdentity } from './api/index'
import type { SessionInfo } from './api/index'

const who = ref(localStorage.getItem('blyb.who') || '')
const roles = ref<string[]>(safeRoles())
const scopes = ref<string[]>(safeScopes())
const loggedIn = ref(false)
const passwordSession = ref(false)
const showPassword = ref(false), oldPassword = ref(''), newPassword = ref(''), repeatPassword = ref('')
const passwordError = ref(''), passwordBusy = ref(false)

function safeRoles(): string[] {
  try { return JSON.parse(localStorage.getItem('blyb.roles') || '[]') as string[] }
  catch { return [] }
}
function safeScopes(): string[] {
  try { return JSON.parse(localStorage.getItem('blyb.scopes') || '[]') as string[] }
  catch { return [] }
}

// 登录时序修复（FIX2-07 保留）：带旧 token 直接进页面先复验；失效回登录态。
// RT-01：me 返回后同步刷新本地 roles/scopes（管理员改角色/范围后侧栏即时更新）。
function applySession(s: SessionInfo): void {
  persistIdentity(s)
  roles.value = safeRoles()
  scopes.value = safeScopes()
}
onMounted(async () => {
  window.addEventListener('blyb:unauthorized', resetLogin)
  if (!token()) return
  try {
    const s = await currentUser()
    passwordSession.value = !!s.password_session
    applySession(s)
    onLogin(s.display_name || s.code)
  } catch { resetLogin() }
})
onUnmounted(() => window.removeEventListener('blyb:unauthorized', resetLogin))

function onLogin(name: string) {
  who.value = name
  localStorage.setItem('blyb.who', name)
  roles.value = safeRoles()
  scopes.value = safeScopes()
  loggedIn.value = true
  currentUser().then(s => {
    passwordSession.value = !!s.password_session
    applySession(s)
  })
    .catch(() => { /* 复验失败由 401 事件统一处理 */ })
}
function resetLogin() {
  clearLogin()
  showPassword.value = false
  who.value = ''
  roles.value = []
  scopes.value = []
  loggedIn.value = false
  passwordSession.value = false
}
async function logout() {
  try { await apiLogout() } catch { /* 离线也清本地态 */ }
  finally { resetLogin() }
}
async function savePassword() {
  passwordError.value = ''
  if (newPassword.value !== repeatPassword.value) { passwordError.value = '两次输入的新密码不一致'; return }
  passwordBusy.value = true
  try {
    await changePassword(oldPassword.value, newPassword.value)
    resetLogin()
  } catch (x: any) { passwordError.value = x.message || '修改失败' }
  finally { passwordBusy.value = false }
}
</script>

<style scoped>
.login-wrap { min-height: 100vh; display: grid; place-items: center; background: var(--c-bg); }
.pw-label { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
.err { color: var(--c-danger); }
</style>
