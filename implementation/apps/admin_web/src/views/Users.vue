<!-- LT-06B P3 用户与角色（platform_admin）：列表 + 新建/改角色/重置密码/锁定。
     业务范围多选来自 report-targets；"*"=全部业务；新建默认无业务。 -->
<template>
  <div>
    <PageHeader title="用户与角色" subtitle="控制台账号管理（药品专页/机器令牌鉴权不受影响）">
      <template #actions>
        <button @click="openCreate">新建用户</button>
      </template>
    </PageHeader>
    <DataTable :columns="cols" :rows="rows" :loading="loading" :error="error"
               @retry="load" empty-text="暂无控制台用户">
      <template #roles="{ value }">
        <span v-for="r in value" :key="r" class="role-chip">{{ roleLabel(r) }}</span>
      </template>
      <template #scopes="{ value }">
        <span v-if="value.includes('*')" class="scope-all">全部业务</span>
        <span v-else-if="!value.length" class="muted">（无业务）</span>
        <span v-else>{{ value.join('、') }}</span>
      </template>
      <template #locked="{ row }">
        <StatusTag :status="row.locked ? 'LOCKED' : 'ACTIVE'" />
      </template>
      <template #actions="{ row }">
        <button class="ghost" @click.stop="editRoles(row)">角色/范围</button>
        <button class="ghost" @click.stop="resetPwd(row)">重置密码</button>
        <button v-if="!row.locked" class="ghost danger"
                @click.stop="lock(row)">锁定</button>
        <button v-else class="ghost" @click.stop="unlock(row)">解锁</button>
      </template>
    </DataTable>

    <!-- 新建 -->
    <Modal :open="creating" title="新建用户" @close="creating = false">
      <label>用户名（3—32 位字母/数字/下划线）</label>
      <input v-model="n.username" />
      <label>显示名</label>
      <input v-model="n.display_name" />
      <label>初始密码（≥12 位）</label>
      <input v-model="n.password" type="password" />
      <label>角色</label>
      <div class="checks">
        <label v-for="r in ASSIGNABLE" :key="r" class="check">
          <input type="checkbox" :value="r" v-model="n.roles" /> {{ roleLabel(r) }}
        </label>
      </div>
      <label>业务范围（不选=无业务；* 在编辑中可改）</label>
      <div class="checks">
        <label v-for="t in targets" :key="t.code" class="check">
          <input type="checkbox" :value="t.code" v-model="n.scopes" />
          {{ t.name }}
        </label>
      </div>
      <div class="modal-actions">
        <button class="ghost" @click="creating = false">取消</button>
        <button :disabled="nBusy" @click="doCreate">创建</button>
      </div>
    </Modal>

    <!-- 改角色/范围 -->
    <Modal :open="editing" :title="`编辑 ${editRow?.username ?? ''}`"
           @close="editing = false">
      <template v-if="editRow">
        <label>角色</label>
        <div class="checks">
          <label v-for="r in ASSIGNABLE" :key="r" class="check">
            <input type="checkbox" :value="r" v-model="e.roles" /> {{ roleLabel(r) }}
          </label>
        </div>
        <label>业务范围</label>
        <div class="checks">
          <label class="check">
            <input type="checkbox" value="*" v-model="e.scopes" /> 全部业务（*）
          </label>
          <label v-for="t in targets" :key="t.code" class="check">
            <input type="checkbox" :value="t.code" v-model="e.scopes" />
            {{ t.name }}
          </label>
        </div>
        <div class="modal-actions">
          <button class="ghost" @click="editing = false">取消</button>
          <button :disabled="eBusy" @click="doEditRoles">保存</button>
        </div>
      </template>
    </Modal>

    <!-- 重置密码（RT-01：替代 window.prompt） -->
    <Modal :open="resetting" :title="`重置密码 ${resetRow?.username ?? ''}`"
           @close="resetting = false">
      <label>新密码（≥12 位；重置后该用户全部会话立即失效）</label>
      <input v-model="resetValue" type="password" autocomplete="new-password" />
      <div class="modal-actions">
        <button class="ghost" @click="resetting = false">取消</button>
        <button :disabled="resetBusy || resetValue.length < 12"
                @click="doResetPwd">确认重置</button>
      </div>
    </Modal>

    <!-- 锁定确认（RT-01：替代 window.confirm） -->
    <ConfirmDialog :open="lockConfirm.open" :title="`锁定 ${lockConfirm.username}？`"
                   :busy="lockConfirm.busy" confirm-text="确认锁定"
                   @update:open="lockConfirm.open = $event"
                   @confirm="doLock">该用户的全部会话将立即失效，解锁前无法登录。</ConfirmDialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { listUsers, listTargets, createUser, updateUserRoles, resetUserPassword, lockUser, unlockUser } from '../api/index'
import type { ConsoleUser, ReportTarget } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import StatusTag from '../components/StatusTag.vue'
import Modal from '../components/Modal.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { toast } from '../components/Toast'
import { formatHospitalTime } from '../utils/time'
import { buildCreateUserPayload } from '../utils/payloads'

const ASSIGNABLE = ['platform_admin', 'config_editor', 'business_reviewer',
  'operator', 'auditor']
const ROLE_LABELS: Record<string, string> = {
  platform_admin: '平台管理员', config_editor: '配置编辑',
  business_reviewer: '业务复核', operator: '运维操作', auditor: '审计',
  service: '服务（不可分配）', viewer: '只读',
}

const rows = ref<ConsoleUser[]>([])
const targets = ref<ReportTarget[]>([])
const loading = ref(true)
const error = ref('')
const creating = ref(false)
const editing = ref(false)
const editRow = ref<ConsoleUser | null>(null)
const nBusy = ref(false)
const eBusy = ref(false)
const n = ref({ username: '', display_name: '', password: '', roles: [] as string[],
  scopes: [] as string[] })
const e = ref({ roles: [] as string[], scopes: [] as string[] })
const resetting = ref(false)
const resetRow = ref<ConsoleUser | null>(null)
const resetValue = ref('')
const resetBusy = ref(false)
const lockConfirm = ref({ open: false, busy: false, username: '' })

const cols = [
  { key: 'username', title: '用户名', width: '140px' },
  { key: 'display_name', title: '显示名', width: '120px' },
  { key: 'roles', title: '角色', slot: 'roles' as const },
  { key: 'target_scopes', title: '业务范围', slot: 'scopes' as const },
  { key: 'locked', title: '状态', width: '90px', slot: 'locked' as const },
  { key: 'password_changed_at', title: '最近改密', width: '150px',
    formatter: (r: ConsoleUser) => formatHospitalTime(r.password_changed_at) },
  { key: 'actions', title: '操作', slot: 'actions' as const, width: '280px' },
]

function roleLabel(r: string): string { return ROLE_LABELS[r] ?? r }

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    ;[rows.value, targets.value] = await Promise.all([
      listUsers(), listTargets()])
  } catch (err: any) { error.value = String(err?.message ?? err) }
  finally { loading.value = false }
}

function openCreate(): void {
  n.value = { username: '', display_name: '', password: '', roles: ['operator'],
    scopes: [] }
  creating.value = true
}
async function doCreate(): Promise<void> {
  nBusy.value = true
  try {
    // RT-01：后端 system_routes 读 target_scopes（不是 scopes）——表单内
    // 复选框绑定的 scopes 必须映射过去，否则勾选的业务范围被静默丢弃。
    await createUser(buildCreateUserPayload(n.value))
    creating.value = false
    toast.success('用户已创建（默认无业务范围，可再编辑授权）')
    load()
  } catch (err: any) { toast.error(`创建失败：${err?.message ?? err}`) }
  finally { nBusy.value = false }
}
function editRoles(row: ConsoleUser): void {
  editRow.value = row
  e.value = { roles: [...row.roles], scopes: [...row.target_scopes] }
  editing.value = true
}
async function doEditRoles(): Promise<void> {
  if (!editRow.value) return
  eBusy.value = true
  try {
    await updateUserRoles(editRow.value.username, {
      roles: e.value.roles.length ? e.value.roles : ['operator'],
      target_scopes: e.value.scopes })
    editing.value = false
    toast.success('角色与业务范围已保存')
    load()
  } catch (err: any) { toast.error(`保存失败（最后一个管理员不可移除）：${err?.message ?? err}`) }
  finally { eBusy.value = false }
}
function resetPwd(row: ConsoleUser): void {
  resetRow.value = row
  resetValue.value = ''
  resetting.value = true
}
async function doResetPwd(): Promise<void> {
  if (!resetRow.value || resetValue.value.length < 12) return
  resetBusy.value = true
  try {
    const r = await resetUserPassword(resetRow.value.username, resetValue.value)
    resetting.value = false
    resetValue.value = ''
    toast.success(`已重置并撤销 ${r.sessions_revoked} 个会话`)
  } catch (err: any) { toast.error(String(err?.message ?? err)) }
  finally { resetBusy.value = false }
}
function lock(row: ConsoleUser): void {
  lockConfirm.value = { open: true, busy: false, username: row.username }
}
async function doLock(): Promise<void> {
  lockConfirm.value.busy = true
  try {
    await lockUser(lockConfirm.value.username)
    lockConfirm.value.open = false
    toast.success('已锁定，其全部会话已失效')
    load()
  } catch (err: any) { toast.error(String(err?.message ?? err)) }
  finally { lockConfirm.value.busy = false }
}
async function unlock(row: ConsoleUser): Promise<void> {
  try { await unlockUser(row.username); toast.success('已解锁'); load() }
  catch (err: any) { toast.error(String(err?.message ?? err)) }
}
onMounted(load)
</script>

<style scoped>
.role-chip { background: var(--bg-alt, #f3f4f6); border-radius: 4px;
  padding: 1px 6px; font-size: 12px; margin-right: 4px; }
.scope-all { color: #1d4ed8; }
.checks { display: flex; flex-wrap: wrap; gap: 6px 14px; }
.check { font-size: 13px; display: flex; gap: 4px; align-items: center; }
label { display: block; font-size: 13px; margin: 8px 0 2px; }
input[type="text"], input[type="password"], input:not([type]) {
  width: 100%; box-sizing: border-box; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
button.danger { color: #b91c1c; }
</style>
