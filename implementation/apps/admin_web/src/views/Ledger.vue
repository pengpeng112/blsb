<!-- LT-06B P1 提交与回执检索：业务下拉（query 同步，支持从驾驶舱深链）。
     RT-01：route.query.bucket 深链过滤透传（驾驶舱"未知>N"跳
     /ledger?target=xxx&bucket=UNKNOWN 时真正自动选中 UNKNOWN）。 -->
<template>
  <div>
    <PageHeader title="提交与回执" subtitle="统一台账（先筛后限；全部条件由后端执行）" />
    <div class="target-pick">
      <label>业务</label>
      <select v-model="target" @change="reload">
        <option v-for="t in targets" :key="t.code" :value="t.code">
          {{ t.name }}（{{ t.code }}）
        </option>
      </select>
    </div>
    <EmptyState v-if="!targets.length" text="当前账号未被授权任何业务" />
    <LedgerPane v-else-if="target" :key="target" :target="target"
                :initial-bucket="bucketFromQuery" :can-resolve="canResolve" />
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listTargets } from '../api/index'
import type { ReportTarget } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import LedgerPane from './target/LedgerPane.vue'

const route = useRoute()
const router = useRouter()
const targets = ref<ReportTarget[]>([])
const target = ref(String(route.query.target ?? ''))
const bucketFromQuery = computed(() => String(route.query.bucket ?? ''))

const canResolve = computed(() => {
  try {
    const r = JSON.parse(localStorage.getItem('blyb.roles') || '[]')
    return r.includes('platform_admin') || r.includes('business_reviewer')
  } catch { return false }
})

watch(target, v => router.replace({ query: { ...route.query, target: v } }))
// 深链 bucket 变化（如驾驶舱再次跳转）由 LedgerPane :key=target 变化重挂载，
// 或经 initial-bucket 传入（同一业务内变化由 LedgerPane 内部 watch 处理）。

async function reload(): Promise<void> {
  // LedgerPane :key=target 自行重载；此处只同步 URL
}
onMounted(async () => {
  try {
    targets.value = await listTargets()
    if (!target.value && targets.value.length) {
      const prod = targets.value.find(t => t.lifecycle === 'PRODUCTION')
      target.value = (prod ?? targets.value[0]).code
    }
  } catch { /* 错误由 LedgerPane/EmptyState 呈现 */ }
})
</script>
<style scoped>
.target-pick { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }
.target-pick label { font-size: 13px; color: var(--muted, #6b7280); }
</style>
