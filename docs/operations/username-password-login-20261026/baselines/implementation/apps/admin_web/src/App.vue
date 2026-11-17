<template>
  <div class="layout">
    <aside v-if="loggedIn">
      <h1>归集上报平台</h1>
      <nav>
        <router-link v-for="[id, label] in nav" :key="id" :to="'/' + id">{{ label }}</router-link>
      </nav>
      <div class="side-footer">
        <span>{{ who }}</span>
        <button @click="logout">退出</button>
      </div>
    </aside>
    <main>
      <Login v-if="!loggedIn" @done="onLogin" />
      <router-view v-else />
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { nav } from './main'
import Login from './views/Login.vue'
import { login, token } from './api'

const who = ref(localStorage.getItem('blyb.who') || '')
const loggedIn = ref(!!localStorage.getItem('blyb.token'))

// 登录时序修复（FIX2-07）：带着旧 token 直接进页面的场景，先低成本复验；
// 失效（401/后端地址已改）立即回到登录页，而不是让每个页面各自报错。
onMounted(async () => {
  if (!loggedIn.value) return
  try { await login(token()) }
  catch { logout() }
})

function onLogin(name: string) {
  who.value = name
  localStorage.setItem('blyb.who', name)
  loggedIn.value = true
}
function logout() {
  localStorage.removeItem('blyb.token')
  who.value = ''
  loggedIn.value = false
}
</script>

<style>
* { box-sizing: border-box; margin: 0; }
body { font: 14px/1.5 "Segoe UI", "Microsoft YaHei", sans-serif; background: #f4f6f8; color: #1c2430; }
.layout { display: flex; min-height: 100vh; }
aside { width: 190px; background: #10233f; color: #cfe0f5; padding: 14px 10px; display: flex; flex-direction: column; }
aside h1 { font-size: 15px; color: #fff; margin-bottom: 14px; padding: 0 6px; }
aside nav { display: flex; flex-direction: column; gap: 2px; flex: 1; }
aside nav a { color: #cfe0f5; text-decoration: none; padding: 7px 10px; border-radius: 6px; font-size: 13px; }
aside nav a.router-link-active { background: #1d3f70; color: #fff; }
.side-footer { font-size: 12px; padding: 8px 6px; display: flex; justify-content: space-between; align-items: center; }
.side-footer button { background: none; border: 1px solid #3a5a86; color: #cfe0f5; border-radius: 4px; padding: 2px 8px; cursor: pointer; }
main { flex: 1; padding: 20px 26px; overflow: auto; }
h2 { font-size: 17px; margin-bottom: 14px; }
table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; font-size: 13px; }
th { background: #eef2f7; text-align: left; padding: 8px 10px; color: #445; white-space: nowrap; }
td { padding: 7px 10px; border-top: 1px solid #edf0f4; vertical-align: top; }
.card { background: #fff; border-radius: 8px; padding: 14px 16px; margin-bottom: 14px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 12px; margin-bottom: 16px; }
.kpi { background: #fff; border-radius: 8px; padding: 12px 14px; }
.kpi b { display: block; font-size: 22px; margin-top: 4px; }
.kpi span { color: #68758a; font-size: 12px; }
button { background: #1d5bbf; border: 0; color: #fff; border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 13px; }
button.ghost { background: #fff; color: #1d5bbf; border: 1px solid #b8cbe8; }
button:disabled { opacity: .5; cursor: not-allowed; }
input, select, textarea { border: 1px solid #cfd8e3; border-radius: 6px; padding: 6px 8px; font-size: 13px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; background: #e8eef7; color: #33517c; }
.tag.ok { background: #e2f4e6; color: #1d6b33; }
.tag.warn { background: #fdf3df; color: #8a6116; }
.tag.err { background: #fbe4e4; color: #9c2b2b; }
.muted { color: #7b8798; }
pre.json { background: #0e1726; color: #c9e3ff; padding: 10px; border-radius: 8px; overflow: auto; font-size: 12px; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
</style>
