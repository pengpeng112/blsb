<!-- LT-06B P1 上报业务列表；RT-05 U02：新建按钮（仅 platform_admin）→
     Drawer 表单（插件下拉=真实 registry 元数据），字段校验/禁重复提交/
     409 冲突展示/成功局部刷新；编码与枚举按服务端校验，前端不杜撰枚举。 -->
<template>
  <div>
    <PageHeader title="上报业务" subtitle="本院全部上报业务目录（点击查看详情）">
      <template #actions>
        <button v-if="isAdmin" class="ghost" @click="openCreate">新建业务…</button>
      </template>
    </PageHeader>
    <DataTable :columns="cols" :rows="rows" :loading="loading" :error="error"
               row-clickable @retry="load" @row-click="go"
               empty-text="暂无上报业务（未配置范围或未初始化）">
      <template #lifecycle="{ value }">
        <StatusTag :status="String(value)" />
      </template>
      <template #sole="{ value }">
        <StatusTag :status="soleStatus(String(value))" />
      </template>
    </DataTable>

    <!-- U02 新建抽屉：服务端校验为准；插件下拉来自 /api/v1/plugins -->
    <Drawer :open="creating" title="新建上报业务" width="520px"
            @close="closeCreate">
      <label>编码 code *（唯一，≤64）</label>
      <input v-model.trim="form.code" placeholder="如 pathology_cloud" />
      <label>名称 name *</label>
      <input v-model.trim="form.name" placeholder="业务显示名" />
      <label>类别 category</label>
      <select v-model="form.category">
        <option value="">（未定）</option>
        <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
      </select>
      <label>主管单位 authority</label>
      <input v-model.trim="form.authority" />
      <label>所属插件 plugin_key *</label>
      <select v-model="form.plugin_key">
        <option value="">— 选择已注册插件 —</option>
        <option v-for="p in plugins" :key="p.key" :value="p.key">
          {{ p.key }}（{{ p.domain }}）</option>
      </select>
      <p v-if="form.plugin_key" class="muted small">
        {{ pluginDesc(form.plugin_key) }}
      </p>
      <label>推送方式 mode</label>
      <select v-model="form.mode">
        <option v-for="m in MODES" :key="m" :value="m">{{ m }}</option>
      </select>
      <label>运行形态 runtime_kind</label>
      <select v-model="form.runtime_kind">
        <option v-for="k in RUNTIME_KINDS" :key="k" :value="k">{{ k }}</option>
      </select>
      <label>负责科室 owner_dept</label>
      <input v-model.trim="form.owner_dept" />
      <label>说明文档路径 readme_path</label>
      <input v-model.trim="form.readme_path" placeholder="如 数据上报/…/README.md" />
      <p v-if="createError" class="form-error">{{ createError }}</p>
      <div class="drawer-actions">
        <button class="ghost" @click="closeCreate">取消</button>
        <button :disabled="busy || !form.code || !form.name || !form.plugin_key"
                @click="submitCreate">
          {{ busy ? '创建中…' : '创建' }}</button>
      </div>
    </Drawer>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listTargets, createTarget, listPlugins } from '../api/index'
import type { ReportTarget } from '../api/types'
import type { PluginMeta } from '../api/reportCenter'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import StatusTag from '../components/StatusTag.vue'
import Drawer from '../components/Drawer.vue'
import { toast } from '../components/Toast'
import { sortTargets } from '../utils/dashboard'
import { formatHospitalTime } from '../utils/time'

// 枚举与 ReportTarget 服务端校验一致（contracts 枚举不前端发明）：
const CATEGORIES = ['病理', '药品', '医保', '传染病', '影像', '运营']
const MODES = ['PUSH', 'PULL']
const RUNTIME_KINDS = ['PLATFORM', 'EXTERNAL', 'MANUAL']

const router = useRouter()
const rows = ref<ReportTarget[]>([])
const loading = ref(true)
const error = ref('')
const plugins = ref<PluginMeta[]>([])

const isAdmin = computed(() => {
  let roles: string[] = []
  try { roles = JSON.parse(localStorage.getItem('blyb.roles') || '[]') } catch { roles = [] }
  return roles.includes('platform_admin')
})

const creating = ref(false)
const busy = ref(false)
const createError = ref('')
const form = reactive<Record<string, string>>({
  code: '', name: '', category: '', authority: '', plugin_key: '',
  mode: 'PUSH', runtime_kind: 'PLATFORM', owner_dept: '', readme_path: '',
})
function pluginDesc(key: string): string {
  return plugins.value.find(p => p.key === key)?.description ?? ''
}

function openCreate(): void {
  form.code = ''; form.name = ''; form.category = ''; form.authority = ''
  form.plugin_key = ''; form.mode = 'PUSH'; form.runtime_kind = 'PLATFORM'
  form.owner_dept = ''; form.readme_path = ''
  createError.value = ''
  creating.value = true
  if (!plugins.value.length) {
    listPlugins().then(ps => { plugins.value = ps })
      .catch(() => { /* 下拉留空并提示：不能杜撰未注册插件 */
        createError.value = '插件元数据加载失败：请刷新后重试（不展示未注册插件）'
      })
  }
}
function closeCreate(): void {
  // 脏输入处理：确认丢弃（避免误触关抽屉丢输入）
  if (form.code || form.name) {
    if (!window.confirm('有未保存的输入，确认关闭并丢弃？')) return
  }
  creating.value = false
}

async function submitCreate(): Promise<void> {
  if (!form.code || !form.name || !form.plugin_key) return
  busy.value = true
  createError.value = ''
  try {
    await createTarget({ ...form } as unknown as Partial<ReportTarget> & { code: string, name: string })
    toast.success(`业务 ${form.code} 已创建`)
    creating.value = false
    await load()                       // 成功后局部刷新
  } catch (e: any) {
    // 409 冲突（code 已存在）不自动覆盖；失败保留输入可重试
    const code = e?.code ?? e?.data?.error?.code
    createError.value = code === 'CONFLICT'
      ? `编码 ${form.code} 已存在（409）：请换编码，不覆盖既有业务`
      : `${String(e?.message ?? e)}（输入已保留，可修正后重试）`
  } finally { busy.value = false }
}

const cols = [
  { key: 'code', title: '编码', width: '180px' },
  { key: 'name', title: '名称' },
  { key: 'category', title: '类别', width: '90px' },
  { key: 'authority', title: '主管单位' },
  { key: 'lifecycle', title: '生命周期', width: '110px',
    slot: 'lifecycle' as const },
  { key: 'runtime_kind', title: '运行形态', width: '100px',
    formatter: (r: ReportTarget) =>
      ({ PLATFORM: '平台', EXTERNAL: '外部程序', MANUAL: '人工' } as Record<string, string>)[r.runtime_kind] ?? r.runtime_kind },
  { key: 'sole_pusher', title: '唯一推送方', width: '110px', slot: 'sole' as const },
  { key: 'owner_dept', title: '负责科室', width: '110px' },
  { key: 'updated_at', title: '更新时间', width: '150px',
    formatter: (r: ReportTarget) => formatHospitalTime(r.updated_at) },
]

function soleStatus(v: string): string {
  return { CONFIRMED: 'OK', UNCONFIRMED: 'WARN', CONFLICT: 'ERROR' }[v] ?? v
}

function go(row: ReportTarget) {
  router.push(`/targets/${encodeURIComponent(row.code)}`)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    rows.value = sortTargets(await listTargets()) as ReportTarget[]
  } catch (e: any) {
    error.value = String(e?.message ?? e)
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.drawer-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.form-error { color: var(--c-danger, #b91c1c); font-size: 12px; margin: 8px 0 0; }
.muted { color: var(--text-muted, #6b7280); }
.small { font-size: 12px; }
label { display: block; font-size: 12px; color: var(--text-muted, #6b7280); margin: 10px 0 2px; }
input, select { width: 100%; box-sizing: border-box; }
</style>
