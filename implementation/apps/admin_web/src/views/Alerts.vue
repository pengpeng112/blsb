<!-- LT-06B P2 告警中心：告警/规则/通道/通知日志/晨报 五 tab。
     规则 params 用 kind 驱动的动态表单（不手写 JSON）；smtp/webhook 本批服务端
     停用（启用/测试被拒），界面如实显示限制。
     RT-01：① 告警改单张合法表格（thead+tbody v-for，不再 <table v-for>）；
     ② 按角色按需加载：非 platform_admin 不自动请求通道（必 403）、非
     admin/auditor 不自动请求通知日志，页签内温和提示；③ window.alert/prompt
     全部换 ConfirmDialog(理由) + toast；④ 时间统一 TimeText（院内东八区）。 -->
<template>
  <div>
    <PageHeader title="告警中心" subtitle="告警与恢复 / 规则 / 通道 / 通知日志 / 晨报" />
    <Tabs v-model="tab" :tabs="visibleTabs" sync-query />

    <!-- 告警 -->
    <div v-if="tab === 'alerts'" class="pane">
      <FilterBar :busy="aLoading" @search="loadAlerts" @reset="resetAlerts">
        <select v-model="aState">
          <option value="">全部状态</option>
          <option value="OPEN">待处理</option>
          <option value="ACKED">已确认</option>
          <option value="RESOLVED">已恢复</option>
        </select>
        <select v-model="aSeverity">
          <option value="">全部级别</option>
          <option value="CRITICAL">严重</option>
          <option value="WARN">警告</option>
          <option value="INFO">提示</option>
        </select>
      </FilterBar>
      <LoadingBlock v-if="aLoading && !alerts.length" />
      <ErrorState v-else-if="aError && !alerts.length" :message="aError" @retry="loadAlerts" />
      <table v-else-if="alerts.length" class="mini-table">
        <thead>
          <tr><th style="width:70px">级别</th><th>内容</th>
            <th style="width:110px">状态</th>
            <th style="width:170px">最近出现</th>
            <th style="width:190px">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="a in alerts" :key="a.id"
              :class="{ 'row-crit-tr': String(a.severity).toUpperCase() === 'CRITICAL' && !a.acked_by }">
            <td><StatusTag :status="String(a.severity).toUpperCase()" /></td>
            <td>
              <b>{{ a.message }}</b>
              <div class="muted small">
                {{ a.rule_code }} · 业务 {{ a.target_code || '（系统）' }} ·
                首次 <TimeText :value="a.first_seen_at" /> ·
                次数 {{ a.occurrences }}
              </div>
            </td>
            <td><StatusTag :status="a.state" /></td>
            <td class="muted"><TimeText :value="a.last_seen_at" /></td>
            <td>
              <button v-if="!a.resolved_at && !a.acked_by" class="ghost"
                      @click="askAck(a)">确认…</button>
              <button v-if="!a.resolved_at && canManualResolve(a)"
                      class="ghost" @click="askResolve(a)">关闭…</button>
            </td>
          </tr>
        </tbody>
      </table>
      <EmptyState v-else text="无匹配告警" />
      <p v-if="aError && alerts.length" class="load-more-err">
        加载更多失败：{{ aError }}
        <button class="ghost mini" @click="loadMoreAlerts">重试</button>
      </p>
      <div v-else-if="aHasMore" class="load-more">
        <button class="ghost" :disabled="aLoadingMore" @click="loadMoreAlerts">
          {{ aLoadingMore ? '加载中…' : '加载更多' }}</button>
      </div>
      <p v-else-if="alerts.length && !aLoading" class="muted small">
        已加载全部匹配告警</p>
    </div>

    <!-- 规则 -->
    <div v-else-if="tab === 'rules'" class="pane">
      <LoadingBlock v-if="rLoading" />
      <ErrorState v-else-if="rError" :message="rError" @retry="loadRules" />
      <EmptyState v-else-if="!rules.length" text="暂无告警规则" />
      <table v-else class="mini-table">
        <thead><tr><th>编码</th><th>名称</th><th>类型</th><th>业务</th>
          <th>级别</th><th>启用</th><th>冷却</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="r in rules" :key="r.code">
            <td><code>{{ r.code }}</code></td>
            <td>{{ r.name }}</td>
            <td>{{ kindLabel(r.kind) }}</td>
            <td>{{ r.target_code || '（系统）' }}</td>
            <td><StatusTag :status="r.severity.toUpperCase()" /></td>
            <td><StatusTag :status="r.enabled ? 'ENABLED' : 'DISABLED'" /></td>
            <td>{{ r.cooldown_minutes }}min</td>
            <td>
              <button v-if="canEditRules" class="ghost"
                      @click="editRule(r)">编辑</button>
              <span v-else class="muted small">（需管理员/配置编辑）</span>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- RT-05 U04：create/update 区分；code 创建后按后端能力只读 -->
      <Drawer :open="ruleEditing"
              :title="ruleMode === 'create' ? '新建规则' : `编辑规则 ${editRuleRow?.code ?? ''}`"
              @close="ruleEditing = false">
        <template v-if="editRuleRow">
          <label>编码 *</label>
          <input v-model.trim="editRuleRow.code" :disabled="ruleMode === 'edit'"
                 placeholder="唯一编码，如 job-failed-daily" />
          <label>类型 *（创建后不可改）</label>
          <select v-model="editRuleRow.kind" :disabled="ruleMode === 'edit'">
            <option value="">— 选择 —</option>
            <option v-for="k in RULE_KIND_KEYS" :key="k" :value="k">
              {{ kindLabel(k) }}</option>
          </select>
          <label>业务作用域 target_code（空=系统级；全局规则不对单业务 scope 用户可见）</label>
          <select v-model="ruleTargetCode">
            <option value="">（系统级）</option>
            <option v-for="t in ruleTargets" :key="t.code" :value="t.code">
              {{ t.code }}（{{ t.name }}）</option>
          </select>
          <label>名称</label>
          <input v-model="editRuleRow.name" />
          <label>级别</label>
          <select v-model="editRuleRow.severity">
            <option>INFO</option><option>WARN</option><option>CRITICAL</option>
          </select>
          <label>启用</label>
          <input type="checkbox" v-model="editRuleRow.enabled" />
          <label>冷却（分钟）</label>
          <input type="number" v-model.number="editRuleRow.cooldown_minutes" min="1" />
          <template v-if="ruleFormDef.fields.length">
            <label>参数（{{ ruleFormDef.label }}）</label>
            <div v-for="fld in ruleFormDef.fields" :key="fld.key" class="field">
              <label :for="'f-' + fld.key">{{ fld.label }}
                <span v-if="fld.required" class="req">*</span></label>
              <input :id="'f-' + fld.key" v-model="ruleFormValues[fld.key]"
                     :type="fld.type === 'number' ? 'number' : 'text'" />
              <p v-if="fld.hint" class="muted small">{{ fld.hint }}</p>
            </div>
          </template>
          <p v-else class="muted">该类型无参数。</p>
          <p v-if="ruleError" class="form-error">{{ ruleError }}</p>
          <div class="drawer-actions">
            <button class="ghost" @click="ruleEditing = false">取消</button>
            <button :disabled="ruleSaving || !editRuleRow.code || !editRuleRow.kind"
                    @click="saveRule">
              {{ ruleMode === 'create' ? '创建' : '保存' }}</button>
          </div>
        </template>
      </Drawer>
      <div class="pane-actions">
        <button v-if="canEditRules" @click="openCreateRule">新建规则…</button>
        <span v-else class="muted small">新建规则需管理员。</span>
      </div>
    </div>

    <!-- 通道（仅 platform_admin 加载；PT-02 企微双通道 + 中文配置摘要 + 预览/探测） -->
    <div v-else-if="tab === 'channels'" class="pane">
      <p class="muted">本地文件通道随时可用；企业微信通道可随时创建、编辑并用「预览卡片」零出网验证；
        真实外发与启用须在生产环境开启外发开关（BLYB_ALLOW_EXTERNAL_NOTIFY=1）；短信邮件通道本批停用。</p>
      <p v-if="!isAdmin" class="muted small">通知通道仅对平台管理员开放。</p>
      <LoadingBlock v-else-if="cLoading" />
      <ErrorState v-else-if="cError" :message="cError" @retry="loadChannels" />
      <EmptyState v-else-if="!channels.length" text="暂无通知通道" />
      <table v-else class="mini-table">
        <thead><tr><th>编码</th><th>类型</th><th>配置摘要</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="c in channels" :key="c.code">
            <td><code>{{ c.code }}</code></td>
            <td>{{ channelTypeLabel(c.type) }}</td>
            <td class="muted small">{{ channelSummary(c) }}</td>
            <td><StatusTag :status="c.enabled ? 'ENABLED' : 'DISABLED'" /></td>
            <td>
              <button class="ghost" @click="doPreview(c)">预览卡片（不出网）</button>
              <button v-if="c.type === 'wecom_relay'" class="ghost"
                      @click="doProbe(c)">连通性探测</button>
              <button class="ghost" :disabled="c.type === 'smtp' || c.type === 'webhook'"
                      @click="doTest(c)">发送测试消息</button>
              <button v-if="isAdmin" class="ghost"
                      @click="openEditChannel(c)">编辑</button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- RT-05 U04：按类型结构化表单；未知字段保留（_extra 并回）；
           秘密只允许 *_env 环境变量引用（变量名按 RT-03 后端正则预校验）；
           PT-02：新增 wecom_relay / wecom_robot 结构化表单（含夜间免打扰/限速） -->
      <Drawer :open="chDrawer"
              :title="chMode === 'create' ? '新建通知通道' : `编辑通道 ${chForm.code}`"
              width="460px" @close="chDrawer = false">
        <label>编码 *</label>
        <input v-model.trim="chForm.code" :disabled="chMode === 'edit'"
               placeholder="如 ops-file / wecom-relay-main" />
        <label>类型 *（创建后不可改）</label>
        <select v-model="chForm.type" :disabled="chMode === 'edit'">
          <option value="">— 选择 —</option>
          <option value="file">本地文件</option>
          <option value="wecom_relay">企业微信网关（医院统一推送平台）</option>
          <option value="wecom_robot">企业微信群机器人</option>
          <option value="smtp">邮件（本批停用）</option>
          <option value="webhook">Webhook（本批停用）</option>
        </select>
        <label>启用</label>
        <input type="checkbox" v-model="chForm.enabled"
               :disabled="chForm.type === 'smtp' || chForm.type === 'webhook'
                          || chForm.type === 'wecom_relay' || chForm.type === 'wecom_robot'" />
        <p v-if="chForm.type === 'smtp' || chForm.type === 'webhook'"
           class="muted small">本批不可启用/测试外发（服务端停用）；保存停用配置不等于启用。</p>
        <p v-else-if="isWecomType"
           class="muted small">启用与实发须生产环境开启外发开关（BLYB_ALLOW_EXTERNAL_NOTIFY=1）；配置与「预览卡片」随时可用。</p>
        <template v-if="chForm.type === 'file'">
          <label>写入路径 path *（相对目录，防穿越）</label>
          <input v-model.trim="chFile.path" placeholder="alerts/alerts.jsonl" />
        </template>
        <template v-if="chForm.type === 'smtp'">
          <label>host *</label>
          <input v-model.trim="chSmtp.host" />
          <label>收件人 to *（逗号分隔）</label>
          <input v-model.trim="chSmtp.to" />
          <label>密码环境变量名 password_env *（只存变量名，不存明文）</label>
          <input v-model.trim="chSmtp.password_env" placeholder="SMTP_PASSWORD" />
        </template>
        <template v-if="chForm.type === 'webhook'">
          <label>URL 环境变量名 url_env *（只存变量名，不存明文）</label>
          <input v-model.trim="chWebhook.url_env" placeholder="OPS_WEBHOOK_URL" />
        </template>
        <template v-if="chForm.type === 'wecom_relay'">
          <label>网关地址环境变量名 url_env *（只存变量名，不存明文）</label>
          <input v-model.trim="chWecomRelay.url_env" placeholder="BLYB_WECOM_RELAY_URL" />
          <label>签名密钥环境变量名 secret_env *（只存变量名，严禁明文）</label>
          <input v-model.trim="chWecomRelay.secret_env" placeholder="BLYB_WECOM_RELAY_SECRET" />
          <label>来源标识 source_id *（小写字母/数字/中划线，如 blyb-platform-alerting）</label>
          <input v-model.trim="chWecomRelay.source_id" placeholder="blyb-platform-alerting" />
          <label>业务分类 category（可选，如 blyb-platform）</label>
          <input v-model.trim="chWecomRelay.category" placeholder="blyb-platform" />
        </template>
        <template v-if="chForm.type === 'wecom_robot'">
          <label>机器人地址环境变量名 url_env *（只存变量名，不存明文）</label>
          <input v-model.trim="chWecomRobot.url_env" placeholder="BLYB_WECOM_ROBOT_URL" />
          <label>卡片格式 msgtype</label>
          <select v-model="chWecomRobot.msgtype">
            <option value="markdown">markdown（带颜色标题）</option>
            <option value="text">text（纯文本）</option>
          </select>
        </template>
        <template v-if="isWecomType">
          <label>夜间免打扰（可选）</label>
          <div class="quiet-row">
            <input type="checkbox" v-model="chQuiet.enabled" />
            <input v-model.trim="chQuiet.start" placeholder="23:00" class="time-input" />
            <span class="muted">至</span>
            <input v-model.trim="chQuiet.end" placeholder="07:30" class="time-input" />
          </div>
          <p class="muted small">开启后夜间窗口仅压制提示/警告级外发（严重级全天候立即推送）；本地文件照常记录，次日自动补发。</p>
          <label>每分钟外发上限 rate_limit_per_min（可选，1–600）</label>
          <input v-model.number="chQuiet.rate" type="number" min="1" max="600"
                 placeholder="如 30" />
        </template>
        <p v-if="chEnvError" class="form-error">{{ chEnvError }}</p>
        <p v-if="chError" class="form-error">
          {{ chError }}
          <span v-if="chRequestId" class="muted">request_id={{ chRequestId }}</span>
        </p>
        <p class="muted small">未知/未展示的配置键按服务端现值原样保留；内联秘密（url/token/password 明文）会被服务端拒绝。</p>
        <div class="drawer-actions">
          <button class="ghost" @click="chDrawer = false">取消</button>
          <button :disabled="chBusy || !chForm.code || !chForm.type" @click="saveChannel">
            {{ chBusy ? '提交中…' : (chMode === 'create' ? '创建' : '保存') }}</button>
        </div>
      </Drawer>

      <!-- PT-02：企微卡片预览弹窗（零出网渲染） -->
      <Drawer :open="previewOpen" title="卡片预览（未发出任何网络请求）"
              width="480px" @close="previewOpen = false">
        <template v-if="previewData">
          <p class="muted small">传输方式：{{ previewData.protocol || '—' }}</p>
          <div class="wecom-card" :class="cardClass">
            <div class="wecom-card-title">{{ previewData.card_title }}</div>
            <pre class="wecom-card-body">{{ previewData.card_body }}</pre>
          </div>
          <p class="muted small">签名头校验（发送时按此构造）：</p>
          <table class="mini-table">
            <tbody>
              <tr v-for="(v, k) in previewData.headers_preview" :key="k">
                <td><code>{{ k }}</code></td><td class="muted">{{ v }}</td>
              </tr>
            </tbody>
          </table>
          <p class="muted small">
            环境变量注入状态：<span v-if="!Object.keys(previewData.env_status).length">（本通道无环境变量引用）</span>
            <template v-for="(v, k) in previewData.env_status" :key="k">
              <code>{{ k }}</code>=<b :class="v === '已注入' ? 'ok-env' : 'missing-env'">{{ v }}</b>&nbsp;
            </template>
          </p>
        </template>
      </Drawer>
      <div class="pane-actions">
        <button v-if="isAdmin" @click="openCreateChannel">新建通道…</button>
      </div>
    </div>

    <!-- 通知日志（platform_admin/auditor 加载） -->
    <div v-else-if="tab === 'logs'" class="pane">
      <p v-if="!canReadLogs" class="muted small">通知日志对平台管理员/审计员开放。</p>
      <LoadingBlock v-else-if="lLoading" />
      <ErrorState v-else-if="lError" :message="lError" @retry="loadLogs" />
      <template v-else>
        <table v-if="logs.length" class="mini-table">
          <thead><tr><th>时间</th><th>通道</th><th>类型</th><th>结果</th><th>错误</th></tr></thead>
          <tbody>
            <tr v-for="l in logs" :key="l.id">
              <td><TimeText :value="l.sent_at" /></td>
              <td>{{ l.channel }}</td>
              <td>{{ l.kind }}</td>
              <td><StatusTag :status="l.ok ? 'OK' : 'ERROR'" /></td>
              <td class="muted">{{ l.error || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-else text="暂无通知记录" />
        <p v-if="lError && logs.length" class="load-more-err">
          加载更多失败：{{ lError }}
          <button class="ghost mini" @click="loadMoreLogs">重试</button>
        </p>
        <div v-else-if="lHasMore" class="load-more">
          <button class="ghost" :disabled="lLoadingMore" @click="loadMoreLogs">
            {{ lLoadingMore ? '加载中…' : '加载更多' }}</button>
        </div>
        <p v-else-if="logs.length" class="muted small">已加载全部通知记录</p>
      </template>
    </div>

    <!-- 晨报 -->
    <div v-else-if="tab === 'digest'" class="pane">
      <LoadingBlock v-if="dLoading" />
      <p v-else-if="dNotice" class="muted small">{{ dNotice }}</p>
      <ErrorState v-else-if="dError" :message="dError" @retry="loadDigest" />
      <EmptyState v-else-if="!digest?.text"
                  text="暂无晨报（系统调度默认停用；启用后每日 08:00 生成）" />
      <template v-else>
        <p class="muted">生成于 <TimeText :value="digest.sent_at" /></p>
        <pre class="digest">{{ digest.text }}</pre>
      </template>
    </div>

    <ConfirmDialog :open="confirm.open" :title="confirm.title"
                   :confirm-text="confirm.confirmText" :busy="confirm.busy"
                   require-reason v-model:reason="confirm.reason"
                   @update:open="confirm.open = $event" @confirm="onConfirm">
      {{ confirm.hint }}
    </ConfirmDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { listAlerts, ackAlert, resolveAlert, listAlertRules, createAlertRule,
  updateAlertRule, listChannels, createChannel, updateChannel, testChannel,
  listNotifyLogs, latestDigest, listTargets } from '../api/index'
import type { ChannelTestPreview } from '../api/ops'
import type { AlertItem, AlertRuleItem, NotifyChannel, NotifyLog } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import Tabs from '../components/Tabs.vue'
import FilterBar from '../components/FilterBar.vue'
import StatusTag from '../components/StatusTag.vue'
import TimeText from '../components/TimeText.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import Drawer from '../components/Drawer.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { toast } from '../components/Toast'
import { RULE_FORMS, formToParams, paramsToForm } from '../utils/params'
import { mergePagedItems } from '../utils/paging'

function roles(): string[] {
  try { return JSON.parse(localStorage.getItem('blyb.roles') || '[]') as string[] }
  catch { return [] }
}
const isAdmin = computed(() => roles().includes('platform_admin'))
const canReadLogs = computed(() =>
  isAdmin.value || roles().includes('auditor'))
const canEditRules = computed(() =>
  isAdmin.value || roles().includes('config_editor'))
const canAck = computed(() => isAdmin.value || ['operator',
  'business_reviewer'].some(r => roles().includes(r)))

const tab = ref('alerts')
const visibleTabs = computed(() => [
  { key: 'alerts', label: '告警' },
  { key: 'rules', label: '规则' },
  ...(isAdmin.value ? [{ key: 'channels', label: '通道' }] : []),
  ...(canReadLogs.value ? [{ key: 'logs', label: '通知日志' }] : []),
  { key: 'digest', label: '晨报' },
])

// ---- 告警（RT-03 S08/S09：分页对象 + 加载更多 + 防旧响应覆盖新筛选）
const alerts = ref<AlertItem[]>([])
const aState = ref('')
const aSeverity = ref('')
const aCursor = ref<string | null>(null)
const aLoading = ref(false)
const aLoadingMore = ref(false)
const aError = ref('')
const aSeq = ref(0)                       // 请求序号：切筛选后旧响应丢弃
const aHasMore = computed(() => aCursor.value != null)

function alertParams(cursor?: string): Record<string, string | number> {
  const q: Record<string, string | number> = { limit: 50 }
  if (aState.value) q.state = aState.value
  if (aSeverity.value) q.severity = aSeverity.value
  if (cursor) q.cursor = cursor
  return q
}
async function loadAlerts(): Promise<void> {
  const seq = ++aSeq.value
  aLoading.value = true
  aError.value = ''
  aCursor.value = null                    // 换筛选重置游标（防旧游标串页）
  try {
    const resp = await listAlerts(alertParams())
    if (seq !== aSeq.value) return        // 旧响应不覆盖新筛选
    alerts.value = resp.items
    aCursor.value = resp.next_cursor ?? null
  } catch (e: any) {
    if (seq === aSeq.value) aError.value = String(e?.message ?? e)
  } finally {
    if (seq === aSeq.value) aLoading.value = false
  }
}
async function loadMoreAlerts(): Promise<void> {
  if (!aCursor.value || aLoadingMore.value) return
  const seq = aSeq.value
  aLoadingMore.value = true
  aError.value = ''
  try {
    const resp = await listAlerts(alertParams(aCursor.value))
    if (seq !== aSeq.value) return
    alerts.value = mergePagedItems(alerts.value, resp.items)  // 实时列表按 ID 去重
    aCursor.value = resp.next_cursor ?? null
  } catch (e: any) {
    if (seq === aSeq.value) aError.value = String(e?.message ?? e)  // 保留已加载
  } finally {
    if (seq === aSeq.value) aLoadingMore.value = false
  }
}
function resetAlerts(): void { aState.value = ''; aSeverity.value = ''; loadAlerts() }
function canManualResolve(a: AlertItem): boolean {
  return ['DATE_EXPIRY', 'EVALUATOR_ERROR'].includes(a.alert_type)
}

type Kind = 'ack' | 'resolve'
const confirm = ref({ open: false, busy: false, reason: '', title: '',
  hint: '', confirmText: '确认', kind: 'ack' as Kind,
  target: null as AlertItem | null })
function askAck(a: AlertItem): void {
  confirm.value = { open: true, busy: false, reason: '', target: a, kind: 'ack',
    title: '确认告警', confirmText: '确认',
    hint: `确认后告警进入已确认（ACKED）态，理由写入审计。` }
}
function askResolve(a: AlertItem): void {
  confirm.value = { open: true, busy: false, reason: '', target: a,
    kind: 'resolve', title: '关闭告警', confirmText: '确认关闭',
    hint: '仅人工类告警可手动关闭；理由写入审计。' }
}
async function onConfirm(reason: string): Promise<void> {
  const c = confirm.value
  if (!c.target) return
  c.busy = true
  try {
    if (c.kind === 'ack') {
      await ackAlert(c.target.id, reason)
      toast.success('告警已确认')
    } else {
      await resolveAlert(c.target.id, reason)
      toast.success('告警已关闭')
    }
    c.open = false
    loadAlerts()
  } catch (e: any) { toast.error(`操作被拒绝：${e?.message ?? e}`) }
  finally { c.busy = false }
}

// ---- 规则
const rules = ref<AlertRuleItem[]>([])
const rLoading = ref(false)
const rError = ref('')
const ruleEditing = ref(false)
const editRuleRow = ref<AlertRuleItem | null>(null)
const formValues = ref<Record<string, string>>({})
const ruleSaving = ref(false)

function kindLabel(kind: string): string {
  return RULE_FORMS.find(f => f.kind === kind)?.label ?? kind
}
async function loadRules(): Promise<void> {
  rLoading.value = true; rError.value = ''
  try { rules.value = await listAlertRules() }
  catch (e: any) { rError.value = String(e?.message ?? e) }
  finally { rLoading.value = false }
}
// RT-05 U04：create/update 区分；code/kind 创建后只读（后端能力为准）
const ruleMode = ref<'create' | 'edit'>('edit')
const ruleError = ref('')
const ruleTargetCode = ref('')
const ruleTargets = ref<{ code: string, name: string }[]>([])
// PT-02 P0 修复：RULE_FORMS 是数组，旧代码对数组做 Object.keys 产生 "0..8" 数字
// 下标、按数字下标取表单定义恒 undefined 导致参数输入框永远消失——
// 改为直接 map 出类型键数组 + find 动态取表单定义（与 formDef 同源）。
const RULE_KIND_KEYS = RULE_FORMS.map(f => f.kind)
const ruleFormDef = computed(() =>
  RULE_FORMS.find(f => f.kind === editRuleRow.value?.kind)
  ?? { kind: '', label: '', fields: [] })
const ruleFormValues = formValues
function openCreateRule(): void {
  ruleMode.value = 'create'
  editRuleRow.value = { code: '', kind: '', name: '', severity: 'WARN',
    enabled: false, cooldown_minutes: 60, params: {}, revision: 0 } as any
  ruleTargetCode.value = ''
  ruleFormValues.value = {}
  ruleError.value = ''
  ruleEditing.value = true
  if (!ruleTargets.value.length) {
    listTargets().then(ts => { ruleTargets.value = ts })
      .catch(() => { /* 下拉留空，不杜撰业务清单 */ })
  }
}
function editRule(r: AlertRuleItem): void {
  ruleMode.value = 'edit'
  editRuleRow.value = { ...r }
  ruleTargetCode.value = r.target_code ?? ''
  ruleFormValues.value = paramsToForm(r.kind, r.params)
  ruleError.value = ''
  ruleEditing.value = true
}
async function saveRule(): Promise<void> {
  if (!editRuleRow.value) return
  ruleSaving.value = true
  ruleError.value = ''
  try {
    const params = formToParams(editRuleRow.value.kind, ruleFormValues.value)
    if (ruleMode.value === 'create') {
      await createAlertRule({
        code: editRuleRow.value.code, kind: editRuleRow.value.kind,
        name: editRuleRow.value.name, severity: editRuleRow.value.severity,
        enabled: editRuleRow.value.enabled,
        cooldown_minutes: editRuleRow.value.cooldown_minutes,
        target_code: ruleTargetCode.value || '', params })
      toast.success('规则已创建')
    } else {
      await updateAlertRule(editRuleRow.value.code, {
        revision: editRuleRow.value.revision,
        name: editRuleRow.value.name,
        target_code: ruleTargetCode.value || '',
        severity: editRuleRow.value.severity,
        enabled: editRuleRow.value.enabled,
        cooldown_minutes: editRuleRow.value.cooldown_minutes, params })
      toast.success('规则已保存')
    }
    ruleEditing.value = false
    loadRules()
  } catch (e: any) {
    const msg = e?.code === 'CONFLICT' || /revision|已存在/.test(String(e?.message))
      ? '保存失败（409 冲突/重名，不自动覆盖）：' + String(e?.message ?? e)
      : `保存失败：${e?.message ?? e}`
    ruleError.value = msg                      // 失败保留输入可重试
    toast.error(ruleError.value)
  } finally { ruleSaving.value = false }
}

// ---- 通道（仅管理员）
const channels = ref<NotifyChannel[]>([])
const cLoading = ref(false)
const cError = ref('')
async function loadChannels(): Promise<void> {
  if (!isAdmin.value) return
  cLoading.value = true
  cError.value = ''
  try { channels.value = await listChannels() }
  catch (e: any) { cError.value = String(e?.message ?? e) }
  finally { cLoading.value = false }
}
// RT-05 U04：通道结构化表单（file/smtp/webhook/wecom 切换；未知键 _extra 保留；
// 环境变量名按 RT-03 后端正则 ^[A-Z][A-Z0-9_]{2,63}$ 预校验；
// PT-02：企微双通道 + 夜间免打扰/限速 + 预览卡片/连通性探测）
const chDrawer = ref(false)
const chMode = ref<'create' | 'edit'>('create')
const chBusy = ref(false)
const chError = ref('')
const chRequestId = ref('')
const chForm = reactive({ code: '', type: '', enabled: false })
const chFile = reactive({ path: '' })
const chSmtp = reactive({ host: '', to: '', password_env: '' })
const chWebhook = reactive({ url_env: '' })
const chWecomRelay = reactive({ url_env: '', secret_env: '', source_id: '', category: '' })
const chWecomRobot = reactive({ url_env: '', msgtype: 'markdown' })
const chQuiet = reactive({ enabled: false, start: '', end: '', rate: undefined as number | undefined })
let chExtra: Record<string, unknown> | null = null   // 未知键保留
const previewOpen = ref(false)
const previewData = ref<ChannelTestPreview | null>(null)
// 预览卡片标题色带（与企业微信实际渲染近似：严重=红、恢复=蓝、其他=黄）
const cardClass = computed(() => {
  const t = previewData.value?.card_title ?? ''
  if (t.includes('严重') || t.includes('升级')) return 'sev-crit'
  if (t.includes('已恢复') || t.includes('晨报')) return 'sev-ok'
  return 'sev-warn'
})
const ENV_NAME_RE = /^[A-Z][A-Z0-9_]{2,63}$/
const SOURCE_ID_RE = /^[a-z0-9][a-z0-9_-]{2,63}$/
const isWecomType = computed(() =>
  chForm.type === 'wecom_relay' || chForm.type === 'wecom_robot')
const TIME_RE = /^\d{1,2}:\d{2}$/
const chEnvError = computed(() => {
  if (chForm.type === 'smtp' && chSmtp.password_env &&
      !ENV_NAME_RE.test(chSmtp.password_env)) {
    return 'password_env 变量名不合法（须字母大写开头，仅大写字母/数字/下划线）'
  }
  if (chForm.type === 'webhook') {
    if (!chWebhook.url_env) return 'Webhook 必填地址环境变量名（不存明文 URL）'
    if (!ENV_NAME_RE.test(chWebhook.url_env)) return 'url_env 变量名不合法'
  }
  if (chForm.type === 'wecom_relay') {
    if (!chWecomRelay.url_env) return '企业微信网关必填地址环境变量名（不存明文 URL）'
    if (!ENV_NAME_RE.test(chWecomRelay.url_env)) return 'url_env 变量名不合法'
    if (!chWecomRelay.secret_env) return '企业微信网关必填签名密钥环境变量名（严禁明文）'
    if (!ENV_NAME_RE.test(chWecomRelay.secret_env)) return 'secret_env 变量名不合法'
    if (!chWecomRelay.source_id) return '必填来源标识 source_id（如 blyb-platform-alerting）'
    if (!SOURCE_ID_RE.test(chWecomRelay.source_id)) return 'source_id 须为小写字母/数字/中划线（3–64 位）'
  }
  if (chForm.type === 'wecom_robot') {
    if (!chWecomRobot.url_env) return '群机器人必填地址环境变量名（不存明文 URL）'
    if (!ENV_NAME_RE.test(chWecomRobot.url_env)) return 'url_env 变量名不合法'
  }
  if (isWecomType.value && chQuiet.enabled) {
    if (!TIME_RE.test(chQuiet.start) || !TIME_RE.test(chQuiet.end)) {
      return '夜间免打扰时间须为 HH:MM（如 23:00 / 07:30）'
    }
  }
  return ''
})
const QUIET_KEYS = new Set(['enabled', 'start', 'end', 'suppress_levels'])
function buildChannelConfig(): Record<string, unknown> {
  const cfg: Record<string, unknown> = { ...(chExtra || {}) }
  if (chForm.type === 'file') cfg.path = chFile.path
  if (chForm.type === 'smtp') {
    cfg.host = chSmtp.host
    cfg.to = chSmtp.to.split(',').map(x => x.trim()).filter(Boolean)
    if (chSmtp.password_env) cfg.password_env = chSmtp.password_env
  }
  if (chForm.type === 'webhook') cfg.url_env = chWebhook.url_env
  if (chForm.type === 'wecom_relay') {
    cfg.url_env = chWecomRelay.url_env
    cfg.secret_env = chWecomRelay.secret_env
    cfg.source_id = chWecomRelay.source_id
    if (chWecomRelay.category) cfg.category = chWecomRelay.category
  }
  if (chForm.type === 'wecom_robot') {
    cfg.url_env = chWecomRobot.url_env
    cfg.msgtype = chWecomRobot.msgtype
  }
  if (isWecomType.value) {
    if (chQuiet.enabled) {
      cfg.quiet_hours = { enabled: true, start: chQuiet.start, end: chQuiet.end,
                          suppress_levels: ['INFO', 'WARN'] }
    } else {
      delete (cfg as any).quiet_hours
    }
    if (typeof chQuiet.rate === 'number' && chQuiet.rate >= 1) {
      cfg.rate_limit_per_min = chQuiet.rate
    } else {
      delete (cfg as any).rate_limit_per_min
    }
  }
  return cfg
}
function channelTypeLabel(t: string): string {
  return ({ file: '本地文件', wecom_relay: '企业微信网关', wecom_robot: '企业微信群机器人',
            smtp: '邮件', webhook: 'Webhook' } as Record<string, string>)[t] ?? t
}
function channelSummary(c: NotifyChannel): string {
  const cfg: any = c.config || {}
  const parts: string[] = []
  if (c.type === 'file') {
    parts.push(`写入 ${cfg.path || 'var/alerts/alerts-YYYYMMDD.jsonl'}`)
  } else if (c.type === 'wecom_relay') {
    parts.push(`源 ${cfg.source_id ?? '—'}`)
    if (cfg.secret_env) parts.push(`密钥变量 ${cfg.secret_env}`)
    if (cfg.url_env) parts.push(`地址变量 ${cfg.url_env}`)
  } else if (c.type === 'wecom_robot') {
    parts.push(`${cfg.msgtype === 'text' ? '纯文本' : 'Markdown'}卡片`)
    if (cfg.url_env) parts.push(`地址变量 ${cfg.url_env}`)
  } else if (c.type === 'smtp') {
    parts.push(`发往 ${Array.isArray(cfg.to) ? cfg.to.join('、') : (cfg.to ?? '—')}`)
  } else if (c.type === 'webhook') {
    if (cfg.url_env) parts.push(`地址变量 ${cfg.url_env}`)
  }
  const qh = cfg.quiet_hours
  if (qh?.enabled) parts.push(`夜间免打扰 ${qh.start}–${qh.end}`)
  if (cfg.rate_limit_per_min) parts.push(`限速 ${cfg.rate_limit_per_min}/分钟`)
  return parts.join(' · ') || '（默认配置）'
}
function openCreateChannel(): void {
  chMode.value = 'create'
  chForm.code = ''; chForm.type = ''; chForm.enabled = false
  chFile.path = ''; chSmtp.host = ''; chSmtp.to = ''; chSmtp.password_env = ''
  chWebhook.url_env = ''
  chWecomRelay.url_env = ''; chWecomRelay.secret_env = ''
  chWecomRelay.source_id = ''; chWecomRelay.category = ''
  chWecomRobot.url_env = ''; chWecomRobot.msgtype = 'markdown'
  chQuiet.enabled = false; chQuiet.start = ''; chQuiet.end = ''; chQuiet.rate = undefined
  chExtra = null; chError.value = ''; chRequestId.value = ''
  chDrawer.value = true
}
function openEditChannel(c: NotifyChannel): void {
  chMode.value = 'edit'
  chForm.code = c.code; chForm.type = c.type
  chForm.enabled = !!c.enabled
  const cfg: any = c.config || {}
  chExtra = null
  const known = new Set(['path', 'host', 'to', 'password_env', 'url_env',
                         'secret_env', 'source_id', 'category', 'msgtype',
                         'quiet_hours', 'rate_limit_per_min'])
  const extra: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(cfg)) {
    if (!known.has(k)) extra[k] = v          // 未知字段不默默丢失
  }
  chExtra = Object.keys(extra).length ? extra : null
  chFile.path = cfg.path ?? ''; chSmtp.host = cfg.host ?? ''
  chSmtp.to = Array.isArray(cfg.to) ? cfg.to.join(',') : (cfg.to ?? '')
  chSmtp.password_env = cfg.password_env ?? ''; chWebhook.url_env = cfg.url_env ?? ''
  chWecomRelay.url_env = cfg.url_env ?? ''; chWecomRelay.secret_env = cfg.secret_env ?? ''
  chWecomRelay.source_id = cfg.source_id ?? ''; chWecomRelay.category = cfg.category ?? ''
  chWecomRobot.url_env = cfg.url_env ?? ''; chWecomRobot.msgtype = cfg.msgtype ?? 'markdown'
  const qh = cfg.quiet_hours
  chQuiet.enabled = !!(qh?.enabled)
  chQuiet.start = qh?.start ?? ''; chQuiet.end = qh?.end ?? ''
  chQuiet.rate = typeof cfg.rate_limit_per_min === 'number' ? cfg.rate_limit_per_min : undefined
  chError.value = ''; chRequestId.value = ''
  chDrawer.value = true
}
async function saveChannel(): Promise<void> {
  if (chEnvError.value) { chError.value = chEnvError.value; return }
  chBusy.value = true
  chError.value = ''; chRequestId.value = ''
  try {
    const config = buildChannelConfig()
    if (chMode.value === 'create') {
      await createChannel({ code: chForm.code, type: chForm.type,
                            config, enabled: chForm.enabled })
      toast.success(`通道 ${chForm.code} 已创建${chForm.enabled ? '并启用' : '（停用态）'}`)
    } else {
      await updateChannel(chForm.code, { config, enabled: chForm.enabled })
      toast.success('通道已保存')
    }
    chDrawer.value = false
    loadChannels()                       // 成功后刷新对应行
  } catch (e: any) {
    chError.value = String(e?.message ?? e)
    chRequestId.value = String(e?.request_id ?? e?.data?.request_id ?? '')
  } finally { chBusy.value = false }
}
async function doPreview(c: NotifyChannel): Promise<void> {
  try {
    const r = await testChannel(c.code, { dry_run: true })
    if (r.ok && r.preview) {
      previewData.value = r.preview
      previewOpen.value = true
    } else {
      toast.error(`预览失败：${r.error ?? '（无错误详情）'}`)
    }
  } catch (e: any) { toast.error(`被拒绝：${e?.message ?? e}`) }
}
async function doProbe(c: NotifyChannel): Promise<void> {
  try {
    const r = await testChannel(c.code, { probe: true })
    if (r.ok) toast.success(`连通性探测成功：${r.detail ?? '网关真达'}`)
    else toast.error(`探测失败：${r.detail ?? r.error ?? '（无详情）'}`)
  } catch (e: any) { toast.error(`被拒绝：${e?.message ?? e}`) }
}
async function doTest(c: NotifyChannel): Promise<void> {
  try {
    const r = await testChannel(c.code)
    // 真实 ok/error：不以 HTTP 200 一律算成功
    if (r.ok) toast.info('测试消息发送成功')
    else toast.error(`测试失败：${r.error ?? '（无错误详情）'}`)
  } catch (e: any) { toast.error(`被拒绝：${e?.message ?? e}`) }
}

// ---- 日志 / 晨报（RT-03 S09：通知日志分页 + 加载更多）
const logs = ref<NotifyLog[]>([])
const lCursor = ref<string | null>(null)
const lLoading = ref(false)
const lLoadingMore = ref(false)
const lError = ref('')
const lHasMore = computed(() => lCursor.value != null)
const digest = ref<{ text: string | null, sent_at: string | null } | null>(null)
const dLoading = ref(false)
const dError = ref('')
const dNotice = ref('')

async function loadLogs(): Promise<void> {
  if (!canReadLogs.value) return
  lLoading.value = true
  lError.value = ''
  lCursor.value = null
  try {
    const resp = await listNotifyLogs()
    logs.value = resp.items
    lCursor.value = resp.next_cursor ?? null
  } catch (e: any) { lError.value = String(e?.message ?? e) }
  finally { lLoading.value = false }
}
async function loadMoreLogs(): Promise<void> {
  if (!lCursor.value || lLoadingMore.value) return
  lLoadingMore.value = true
  lError.value = ''
  try {
    const resp = await listNotifyLogs(undefined, lCursor.value)
    logs.value = mergePagedItems(logs.value, resp.items)
    lCursor.value = resp.next_cursor ?? null
  } catch (e: any) { lError.value = String(e?.message ?? e) }  // 保留已加载
  finally { lLoadingMore.value = false }
}
async function loadDigest(): Promise<void> {
  dLoading.value = true
  dError.value = ''
  dNotice.value = ''
  try { digest.value = await latestDigest() }
  catch (e: any) {
    if (e?.status === 403) dNotice.value = '全院晨报仅对全院范围账号开放'
    else dError.value = String(e?.message ?? e)
  }
  finally { dLoading.value = false }
}

onMounted(() => {
  loadAlerts()
  loadRules()
  loadChannels()
  loadLogs()
  loadDigest()
})
</script>

<style scoped>
.pane { padding-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.mini-table { width: 100%; font-size: 13px; border-collapse: collapse; }
.mini-table th, .mini-table td { text-align: left; padding: 6px 8px;
  border-bottom: 1px solid var(--border, #e5e7eb); vertical-align: top; }
.row-crit-tr { background: #fef2f2; }
.small { font-size: 12px; }
.req { color: #b91c1c; }
.field { display: flex; flex-direction: column; gap: 2px; margin: 6px 0; }
.drawer-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.digest { white-space: pre-wrap; font-size: 12px; background: var(--card, #fafafa);
  padding: 10px; border-radius: 6px; max-height: 420px; overflow: auto; }
.load-more { margin-top: 8px; text-align: center; }
.load-more-err { font-size: 12px; color: var(--c-danger, #b91c1c); }
.quiet-row { display: flex; align-items: center; gap: 6px; margin: 4px 0; }
.time-input { width: 90px; }
.wecom-card { border: 1px solid var(--border, #e5e7eb); border-radius: 8px;
  padding: 10px 12px; margin: 8px 0; background: var(--card, #fafafa); }
.wecom-card-title { font-weight: 600; font-size: 14px; margin-bottom: 6px; }
.wecom-card.sev-crit .wecom-card-title { color: #c0392b; }
.wecom-card.sev-ok .wecom-card-title { color: #2471a3; }
.wecom-card.sev-warn .wecom-card-title { color: #b45309; }
.wecom-card-body { white-space: pre-wrap; font-size: 12px; margin: 0;
  font-family: inherit; }
.ok-env { color: #1a7f37; }
.missing-env { color: #b45309; }
</style>
