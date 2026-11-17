<template>
  <div>
    <h2>药品追溯（码上放心）</h2>
    <p class="muted">
      当前模式：<b :class="modeClass">{{ modeLabel }}</b>
      <span v-if="modeNote">（{{ modeNote }}）</span> —— 按零售目的地配置动态判定；
      发送仍受证据/授权范围/配额门禁约束（EXT-12），UNKNOWN 一律先查询不重发。
      单据键含药房与业务分类，跨药房同号不串单；业务分类不明/0码/未完成进入质量等待，不发送。
    </p>

    <!-- 就绪配置 -->
    <div class="card">
      <h3>接入就绪（证据/缺项）</h3>
      <button class="ghost" @click="loadReadiness">刷新就绪状态</button>
      <p v-if="Object.keys(readinessLayers).length" class="row small">
        分层：<template v-for="(v, k) in readinessLayers" :key="k">
          <b :class="v.status === 'READY' ? 'ok' : (v.status === 'UNKNOWN' ? 'warn' : 'err')">{{ k }}={{ v.status }}</b>&nbsp;
        </template>
        <span class="mono small">（NETWORK=UNKNOWN 不是绿灯：需现场实测 <prod-host>→DMZ443 与 DMZ→平台）</span>
      </p>
      <table v-if="readiness.length">
        <tr><th>Profile</th><th>方法</th><th>目的地</th><th>能力</th><th>证据状态</th><th>缺项</th><th>签名已验</th></tr>
        <template v-for="r in readiness" :key="r.profile + r.label">
          <tr v-for="c in r.capabilities" :key="r.profile + c.capability">
            <td>{{ r.profile }}</td>
            <td class="mono">{{ r.method }}</td>
            <td>
              <span v-if="r.destination_ambiguous" class="err">多目的地（{{ (r.destination_candidates||[]).length }} 个）需显式指定</span>
              <span v-else-if="r.destination">{{ r.destination.code }}{{ r.destination.is_mock ? '（MOCK）' : '（真实）' }} / {{ r.destination.status }}{{ r.destination.config_revision ? '' : ' / 无配置' }}</span>
              <span v-else class="err">未绑定</span>
            </td>
            <td>{{ c.capability }}</td>
            <td><b :class="c.status === 'APPROVED' ? 'ok' : 'warn'">{{ c.status }}</b></td>
            <td>{{ (c.missing || []).join(', ') || '—' }}</td>
            <td>{{ r.signature_verified ? '是' : '否（EXT-07）' }}</td>
          </tr>
        </template>
      </table>
      <div class="row" v-if="canApprove">
        <h4>登记证据（platform_admin）</h4>
        <select v-model="evProfile"><option v-for="p in profileKeys" :key="p" :value="p">{{ p }}</option></select>
        <select v-model="evCapability"><option>collect</option><option>send</option><option>query</option><option>reconcile</option></select>
        <button @click="approveEvidence">按当前缺项登记全部证据（演示）</button>
        <button class="ghost" @click="revokeAll">撤销全部药品证据（停发）</button>
      </div>
      <div class="row" v-if="canApprove && evCapability === 'send'">
        <!-- FIX3-04：send 证据必须精确绑定 config_release 与完整 EXT-12 scope
             （窗口/药房白名单/配额；空值不是通配） -->
        <input v-model="evConfigRelease" placeholder="config_release_id（send 必填，精确绑定）" style="width:280px" />
        <input v-model="evWinBegin" placeholder="授权窗口起 2026-10-01T00:00:00" style="width:200px" />
        <input v-model="evWinEnd" placeholder="授权窗口止 2026-11-01T00:00:00" style="width:200px" />
        <input v-model="evPharmacies" placeholder="药房白名单（逗号分隔，显式枚举）" style="width:220px" />
        <input v-model="evMaxBills" placeholder="配额 max_bills" style="width:120px" />
      </div>
    </div>

    <!-- 定时上报（自动化） -->
    <div class="card">
      <h3>定时上报（自动化）</h3>
      <div class="row">
        <button class="ghost" @click="loadTimed">刷新定时上报</button>
        <span class="muted small">每日 06:10（Asia/Shanghai）处理前一日窗口；此处为只读监控，不触发发送</span>
      </div>
      <p v-if="timedError" class="err small">{{ timedError }}（可点击刷新重试）</p>

      <!-- 授权 -->
      <p v-if="timedScope" class="row small">
        授权：
        <template v-if="timedScope.evidence?.evidence_id">
          窗口 {{ shortDate(timedScope.scope?.window_begin) }} → {{ shortDate(timedScope.scope?.window_end) }}
          ｜ 药房 {{ (timedScope.scope?.pharmacies || []).length }} 家
          ｜ 配额 <span class="mono">{{ timedScope.quota ? timedScope.quota.used + '/' + timedScope.quota.max_bills : '—' }}</span>
          ｜ 有效期至 {{ shortDate(timedScope.evidence?.valid_until) }}
          ｜ 证据 <span class="tag" :class="evClass(timedScope.evidence?.status)">{{ timedScope.evidence?.status }}</span>
          ｜ 目的地 {{ timedScope.destination?.code || '—' }}{{ timedScope.destination?.is_mock ? '（MOCK）' : '' }}
        </template>
        <template v-else>
          <span v-if="timedScope.ambiguous" class="err">多目的地，需在就绪配置中显式指定后才会展示授权</span>
          <span v-else class="warn">send 证据未登记（MISSING）——定时发送将被门禁拦截</span>
        </template>
      </p>

      <!-- 健康 -->
      <p v-if="health" class="row small">
        健康：
        <template v-if="health.empty">
          <span class="muted">尚无运行记录</span>
        </template>
        <template v-else>
          上次运行 {{ health.latest.report_date }}（{{ health.latest.source }}）
          <span class="tag" :class="health.latest.outcome === 'OK' ? 'ok' : 'err'">{{ health.latest.outcome }}</span>
          耗时 {{ fmtMinutes(health.latest.elapsed_seconds) }}
          ｜ 下次预计 {{ health.nextAt }}
          ｜ 连续正常 {{ health.streak }} 天
          <span v-if="health.missing" class="tag err">MISSING：{{ health.yesterdayStr }} 报告未入库（已过 07:30）</span>
        </template>
      </p>

      <!-- 日报表 -->
      <h4>日报表（驱动运行记录）</h4>
      <table v-if="dailyReports.length">
        <tr><th>日期</th><th>来源</th><th class="num">候选</th><th class="num">发送请求</th><th class="num">准备/发送错误</th><th class="num">查询错误</th><th class="num">查询轮次用尽</th><th>结果</th><th class="num">耗时</th><th>操作</th></tr>
        <tr v-for="r in dailyReports" :key="r.report_date + r.source">
          <td class="mono">{{ r.report_date }}</td>
          <td>{{ r.source }}</td>
          <td class="num">{{ r.candidate_count }}</td>
          <td class="num">{{ r.sent }}</td>
          <td class="num"><b :class="(r.errors_count || 0) > 0 ? 'err' : ''">{{ r.errors_count }}</b></td>
          <td class="num"><b :class="(r.query_errors_count || 0) > 0 ? 'err' : ''">{{ r.query_errors_count || 0 }}</b></td>
          <td class="num">{{ r.query_skipped_exhausted || 0 }}</td>
          <td><span class="tag" :class="r.outcome === 'OK' ? 'ok' : 'err'">{{ r.outcome }}</span></td>
          <td class="num">{{ fmtMinutes(r.elapsed_seconds) }}</td>
          <td><button class="ghost" @click="showDailyDetail(r.report_date)">{{ detailDate === r.report_date ? '收起' : '详情' }}</button></td>
        </tr>
      </table>
      <p v-else class="muted">尚无运行记录（定时器首跑或日报接口未上线前为空）</p>
      <div v-if="dailyDetail" class="row" style="flex-direction:column;align-items:stretch">
        <h4>日报详情 {{ dailyDetail.report_date }}（{{ dailyDetail.source }}）</h4>
        <p class="small">
          窗口 <span class="mono">{{ (dailyDetail.window[0] || '').slice(0, 16) }} → {{ (dailyDetail.window[1] || '').slice(0, 16) }}</span>
          ｜ 候选 {{ dailyDetail.candidate_count }}
          ｜ 采集 <span class="mono">{{ dailyDetail.collection?.state }}</span>
          <span v-if="dailyDetail.collection?.summary" class="mono small">（{{ JSON.stringify(dailyDetail.collection.summary) }}）</span>
          ｜ 发送 sent={{ dailyDetail.send?.sent }} prepared_new={{ dailyDetail.send?.prepared_new }} resumed={{ dailyDetail.send?.resumed }} skipped_terminal={{ dailyDetail.send?.skipped_terminal }}
          ｜ 查询补进 {{ dailyDetail.query_stats?.queried }}
          ｜ 轮次用尽待人工 {{ dailyDetail.query_stats?.skipped_exhausted || 0 }}
          ｜ 已有查询在途 {{ dailyDetail.query_stats?.skipped_inflight || 0 }}
          <span v-if="dailyDetail.send?.aborted_reason" class="tag err">中止：{{ dailyDetail.send.aborted_reason }}</span>
        </p>
        <p v-if="dailyDetail.send?.errors_truncated" class="small muted">错误共 {{ dailyDetail.send.errors_total }} 项，下面列出前 {{ dailyDetail.send.errors.length }} 项；完整明细保存在服务器本次运行回执中。</p>
        <table v-if="(dailyDetail.send?.errors || []).length">
          <tr><th>提交/单据</th><th>步骤</th><th>错误</th></tr>
          <tr v-for="(e, i) in dailyDetail.send.errors" :key="i">
            <td class="mono">{{ e.submission || e.bill || '—' }}</td>
            <td>{{ e.step }}</td>
            <td class="mono small">{{ trunc200(JSON.stringify(e.res || e)) }}</td>
          </tr>
        </table>
        <p v-if="(dailyDetail.query_stats?.errors_total || dailyDetail.query_stats?.errors?.length || 0) > 0" class="small err">查询错误共 {{ dailyDetail.query_stats.errors_total || dailyDetail.query_stats.errors.length }} 项；下面显示保留的错误样例。发送请求数不等于平台确认成功数。</p>
        <table v-if="(dailyDetail.query_stats?.errors || []).length">
          <tr><th>提交</th><th>查询错误</th></tr>
          <tr v-for="(e, i) in dailyDetail.query_stats.errors" :key="i">
            <td class="mono">{{ e.submission || '—' }}</td>
            <td class="mono small">{{ trunc200(JSON.stringify(e.res || e)) }}</td>
          </tr>
        </table>
      </div>

      <!-- 每日实际（平台终态） -->
      <h4>每日实际（平台终态，含次日查询补进）</h4>
      <table v-if="statsDays.length">
        <tr><th>日期</th><th class="num">CONFIRMED</th><th class="num">ACCEPTED</th><th class="num">PARTIAL</th><th class="num">REJECTED</th><th class="num">UNKNOWN</th><th class="num">其他</th><th class="num">合计</th><th class="num">尝试失败</th></tr>
        <tr v-for="d in statsDays" :key="d.date">
          <td class="mono">{{ d.date }}</td>
          <td class="num">{{ d.by_state?.CONFIRMED || 0 }}</td>
          <td class="num">{{ d.by_state?.ACCEPTED || 0 }}</td>
          <td class="num">{{ d.by_state?.PARTIAL || 0 }}</td>
          <td class="num">{{ d.by_state?.REJECTED || 0 }}</td>
          <td class="num"><b :class="(d.by_state?.UNKNOWN || 0) > 0 ? 'err' : ''">{{ d.by_state?.UNKNOWN || 0 }}</b></td>
          <td class="num muted">{{ otherStates(d) }}</td>
          <td class="num">{{ d.total }}</td>
          <td class="num">{{ (d.attempts?.http_error || 0) + (d.attempts?.other || 0) }}</td>
        </tr>
      </table>
      <p v-else class="muted">近 {{ statsDaysParam }} 天无提交记录</p>
      <p class="muted small">日期按提交创建时间（UTC）分组；上方日报表日期为业务窗口（GMT+8）。两表口径不同，同日数字不可直接对齐。</p>

      <!-- 异常提交 -->
      <h4>异常提交（UNKNOWN/REJECTED/PARTIAL 或最近尝试非 ACCEPTED）</h4>
      <table v-if="problemSubs.length">
        <tr><th>提交</th><th>状态</th><th>最近尝试</th><th class="num">HTTP</th><th>单号</th><th>时间</th><th>操作</th></tr>
        <tr v-for="s in problemSubs" :key="s.id">
          <td class="mono">{{ s.id.slice(0, 8) }}</td>
          <td><span class="tag" :class="s.state === 'UNKNOWN' ? 'err' : 'warn'">{{ s.state }}</span></td>
          <td>{{ s.attempt_outcome || '—' }}</td>
          <td class="num">{{ s.attempt_http_status ?? '—' }}</td>
          <td class="mono">{{ s.source_bill_no || s.bill_code || '—' }}</td>
          <td class="mono">{{ (s.updated_at || '').slice(0, 16) }}</td>
          <td><button v-if="canOperate" class="ghost" @click="queryProblem(s)">查询状态</button></td>
        </tr>
      </table>
      <p v-else class="muted">近 {{ outcomesDays }} 天无异常提交</p>
    </div>

    <!-- 采集 -->
    <div class="card">
      <h3>采集运行（合成视图演练）</h3>
      <div class="row">
        <select v-model="connectorId"><option v-for="c in connectors" :key="c.id" :value="c.id">{{ c.plugin_id }} / {{ c.name }}</option></select>
        <input v-model="winBegin" placeholder="2026-10-12T00:00:00" style="width:200px" />
        <input v-model="winEnd" placeholder="2026-10-13T00:00:00" style="width:200px" />
        <button @click="runCollect" :disabled="!connectorId">发起采集（半开窗口）</button>
        <button class="ghost" @click="loadRuns">刷新运行</button>
      </div>
      <table v-if="runs.length">
        <tr><th>运行</th><th>状态</th><th>窗口</th><th>摘要</th></tr>
        <tr v-for="r in runs" :key="r.id">
          <td class="mono">{{ r.id.slice(0, 8) }}</td>
          <td>{{ r.state }}</td>
          <td class="mono">{{ (r.window[0] || '').slice(0, 16) }} → {{ (r.window[1] || '').slice(0, 16) }}</td>
          <td class="mono">{{ JSON.stringify(r.summary) }}</td>
        </tr>
      </table>
    </div>

    <!-- 单据 -->
    <div class="card">
      <h3>单据</h3>
      <div class="row">
        <input v-model="qPharmacy" placeholder="药房代码过滤" style="width:130px" />
        <select v-model="qKind">
          <option value="">全部分类</option>
          <option v-for="k in kinds" :key="k" :value="k">{{ k }}</option>
        </select>
        <button class="ghost" @click="loadBills">刷新</button>
      </div>
      <table v-if="bills.length">
        <tr><th>药房</th><th>分类</th><th>单号</th><th>来源版本</th><th>质量</th><th>码数</th><th>完成</th><th>业务时间</th><th>操作</th></tr>
        <tr v-for="b in bills" :key="b.id">
          <td>{{ b.pharmacy_code }}</td>
          <td>{{ b.business_kind }}</td>
          <td class="mono">{{ b.source_bill_no }}</td>
          <td class="mono">{{ b.source_revision }}</td>
          <td><b :class="b.quality_state === 'VALID' ? 'ok' : (b.quality_state === 'WAITING' ? 'warn' : 'err')">{{ b.quality_state || '—' }}</b></td>
          <td>{{ b.code_count }}</td>
          <td>{{ b.finalized ? '是' : '否' }}</td>
          <td class="mono">{{ (b.bill_time || '').slice(0, 16) }}</td>
          <td>
            <button class="ghost" @click="preview(b)">预览(dry-run)</button>
            <button class="ghost" @click="showCodes(b)">码明细</button>
            <button v-if="b.quality_state === 'VALID'" @click="prepare(b)">冻结/准备提交</button>
          </td>
        </tr>
      </table>
      <pre class="json" v-if="previewOut">{{ JSON.stringify(previewOut, null, 2) }}</pre>
      <div v-if="codesFor">
        <h4>码明细（{{ codesFor.bill }}，掩码）</h4>
        <table>
          <tr><th>扫码记录</th><th>明细行</th><th>码（掩码）</th><th>层级</th><th>数量</th><th>删除</th></tr>
          <tr v-for="c in codes" :key="c.source_code_record_id">
            <td class="mono">{{ c.source_code_record_id }}</td>
            <td class="mono">{{ c.source_line_id }}</td>
            <td class="mono">{{ c.trace_code_masked }}</td>
            <td>{{ c.code_level || '—' }}</td>
            <td>{{ c.quantity }}{{ c.unit }}</td>
            <td>{{ c.is_deleted ? '是' : '否' }}</td>
          </tr>
        </table>
      </div>
    </div>

    <!-- 提交/回执 -->
    <div class="card">
      <h3>提交与回执</h3>
      <button class="ghost" @click="loadSubs">刷新提交</button>
      <table v-if="subs.length">
        <tr><th>提交</th><th>状态</th><th>快照摘要</th><th>创建</th><th>操作</th></tr>
        <tr v-for="s in subs" :key="s.id">
          <td class="mono">{{ s.id.slice(0, 8) }}</td>
          <td><b :class="stateClass(s.state)">{{ s.state }}</b></td>
          <td class="mono">{{ s.snapshot_digest?.slice(0, 12) }}</td>
          <td class="mono">{{ (s.created_at || '').slice(0, 19) }}</td>
          <td>
            <button v-if="s.state === 'PREPARED'" @click="send(s)">发送（需批准）</button>
            <button class="ghost" @click="query(s)">查询状态</button>
          </td>
        </tr>
      </table>
      <p class="muted">UNKNOWN＝结果未知（可能已达平台）：一律先查询，不自动重发。</p>
    </div>

    <!-- 对账 -->
    <div class="card">
      <h3>对账（单据级；码级待 EXT-09）</h3>
      <div class="row">
        <select v-model="reconProfile"><option v-for="p in profileKeys" :key="p" :value="p">{{ p }}</option></select>
        <button @click="runRecon">发起对账</button>
        <button class="ghost" @click="loadRecon">刷新最近对账</button>
      </div>
      <table v-if="recon">
        <tr><th>Run</th><th>状态</th><th>摘要</th><th>平台快照</th><th>码级核验</th></tr>
        <tr><td class="mono">{{ recon.id.slice(0, 8) }}</td>
            <td><b :class="recon.state === 'COMPLETE' ? 'ok' : 'warn'">{{ recon.state }}</b></td>
            <td class="mono">{{ JSON.stringify(recon.summary) }}</td>
            <td>{{ recon.scope?.platform_ok === false ? '不可用（未核验：' + (recon.scope?.remote_error || '') + '）' : '可用' }}</td>
            <td>{{ recon.scope?.code_level_verified ? '已核验' : '未核验（EXT-09）' }}</td></tr>
      </table>
      <table v-if="reconItems.length">
        <tr><th>单据</th><th>期望</th><th>实际</th><th>差异类型</th><th>处置</th><th>操作</th></tr>
        <tr v-for="i in reconItems" :key="i.id">
          <td class="mono">{{ i.item_key }}</td><td>{{ i.expected_state }}</td>
          <td>{{ i.actual_state }}</td><td>{{ i.detail?.kind }}</td>
          <td>{{ i.disposition }}</td>
          <td>
            <template v-if="i.disposition === 'PENDING'">
              <input v-model="dispReason[i.id]" placeholder="处置理由（必填）" style="width:150px" />
              <button @click="dispose(i, 'COMPENSATE')">批准补偿</button>
              <button class="ghost" @click="dispose(i, 'IGNORE')">忽略</button>
            </template>
          </td>
        </tr>
      </table>
    </div>

    <pre class="json" v-if="msg">{{ msg }}</pre>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'

const profileKeys = ['alihealth_yljg_inout_v1', 'alihealth_yljg_retail_v1']
const kinds = ['SUPPLIER_IN', 'SUPPLIER_RETURN', 'PATIENT_DISPENSE', 'PATIENT_RETURN', 'UNKNOWN']

const readiness = ref<any[]>([])
const connectors = ref<any[]>([])
const connectorId = ref('')
const winBegin = ref('2026-10-12T00:00:00')
const winEnd = ref('2026-10-13T00:00:00')
const runs = ref<any[]>([])
const bills = ref<any[]>([])
const qPharmacy = ref(''), qKind = ref('')
const previewOut = ref<any>(null)
const codes = ref<any[]>([])
const codesFor = ref<any>(null)
const subs = ref<any[]>([])
const recon = ref<any>(null)
const reconItems = ref<any[]>([])
const reconProfile = ref(profileKeys[1])
const evProfile = ref(profileKeys[1])
const evCapability = ref('send')
const evConfigRelease = ref('')
const evWinBegin = ref('2026-10-01T00:00:00')
const evWinEnd = ref('2026-11-01T00:00:00')
const evPharmacies = ref('YF01')
const evMaxBills = ref('10')
const dispReason = ref<Record<string, string>>({})
const msg = ref('')

const roles = JSON.parse(localStorage.getItem('blyb.roles') || '[]')
const canApprove = computed(() => roles.includes('platform_admin'))
// 查询/操作类按钮按 operator 显隐（FIX-06：auditor 只读，点击必 403）
const canOperate = computed(() => roles.includes('operator') || roles.includes('platform_admin'))

// ---- 页顶模式横幅（TUI-06）：按零售 profile 目的地 readiness 动态判定
// FIX-06：加载完成前显示「加载中…」，读取失败显示「未知」（不再闪现「未启用」）
const readinessLoaded = ref(false)
const readinessError = ref(false)
const retailDestination = computed(() =>
  readiness.value.find((r: any) => r.profile === 'alihealth_yljg_retail_v1')?.destination || null)
const modeLabel = computed(() => {
  if (!readinessLoaded.value) return '加载中…'
  if (readinessError.value) return '未知（就绪读取失败）'
  const d = retailDestination.value
  if (!d) return '未启用（未绑定目的地）'
  if (d.is_mock) return 'MOCK/合成'
  return d.status === 'ENABLED' ? '正式链路' : `未启用（${d.status}）`
})
const modeClass = computed(() => {
  if (!readinessLoaded.value) return 'muted'
  if (readinessError.value) return 'warn'
  const d = retailDestination.value
  return d && !d.is_mock && d.status === 'ENABLED' ? 'ok' : 'warn'
})
const modeNote = computed(() => {
  const d = retailDestination.value
  if (!d) return ''
  if (d.is_mock) return '仅本地演练，不外发'
  return d.status === 'ENABLED' ? '目的地已启用且非 Mock' : '目的地未启用'
})

// ---- 定时上报面板（TUI-05）：授权/健康/日报/每日实际/异常提交
const timedScope = ref<any>(null)
const dailyReports = ref<any[]>([])
const statsDays = ref<any[]>([])
const problemSubs = ref<any[]>([])
const dailyDetail = ref<any>(null)
const detailDate = ref('')
const statsDaysParam = ref(14)
const outcomesDays = ref(7)

function stateClass(s: string) {
  return s === 'CONFIRMED' ? 'ok' : (s === 'UNKNOWN' || s === 'PARTIAL' ? 'err' : 'warn')
}
function evClass(st: string) {
  return st === 'APPROVED' ? 'ok' : 'err'
}
function shortDate(v?: string) {
  return (v || '').slice(0, 10) || '—'
}
function fmtMinutes(sec?: number) {
  if (!sec || sec <= 0) return '—'
  return sec >= 90 ? Math.round(sec / 60) + ' 分钟' : Math.round(sec) + ' 秒'
}
function trunc200(s: string) {
  return s.length > 200 ? s.slice(0, 200) + '…' : s
}
// 「其他」列（FIX-04）：by_state 中五主状态之外的键求和（PREPARED/SUPERSEDED 等）
const _KNOWN_STATES = ['CONFIRMED', 'ACCEPTED', 'PARTIAL', 'REJECTED', 'UNKNOWN']
function otherStates(d: any) {
  let n = 0
  for (const k in (d.by_state || {})) if (!_KNOWN_STATES.includes(k)) n += d.by_state[k]
  return n
}

// 健康规则（任务书 §5.4）：纯数据推导，不查 systemd
const health = computed(() => {
  const rows = dailyReports.value.filter((r: any) => r.source === 'timer')
  if (!rows.length) return { empty: true }   // 无报告不判 MISSING（可能刚部署）
  const latest = rows[0]                     // 列表按 report_date 倒序
  const maxDate = rows.reduce((m: string, r: any) => r.report_date > m ? r.report_date : m,
                              rows[0].report_date)
  // 连续正常按日期连续性（FIX-05）：从最新一天向前逐日核对；缺整天即停，
  // 不再按行累计（旧行为隔整天缺报仍计数）
  const rmap = new Map(rows.map((r: any) => [r.report_date, r.outcome]))
  const fmt = (x: Date) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`
  let streak = 0
  {
    const [Y, M, D] = maxDate.split('-').map(Number)
    const d = new Date(Y, M - 1, D)
    while (rmap.get(fmt(d)) === 'OK') { streak++; d.setDate(d.getDate() - 1) }
  }
  const [Y, M, D] = maxDate.split('-').map(Number)
  const next = new Date(Y, M - 1, D + 1, 6, 10)   // max(report_date)+1 天 06:10 本地（=院方 GMT+8）
  const today = new Date()
  const y = new Date(today); y.setDate(y.getDate() - 1)
  const yesterdayStr = `${y.getFullYear()}-${String(y.getMonth() + 1).padStart(2, '0')}-${String(y.getDate()).padStart(2, '0')}`
  const deadline = new Date(today); deadline.setHours(7, 30, 0, 0)
  const hasYesterday = rows.some((r: any) => r.report_date === yesterdayStr)
  const missing = !hasYesterday && new Date() > deadline
  return { empty: false, latest, streak, nextAt: `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}-${String(next.getDate()).padStart(2, '0')} 06:10`, missing, yesterdayStr }
})

// 面板错误提示（FIX-06）：不静默吞错——各段失败显示对应文案（「；」连接）
const timedError = ref('')
async function loadTimed() {
  timedError.value = ''
  const errs: string[] = []
  try { timedScope.value = await api.coreGet('/api/v1/drug-trace/send-scope?profile=alihealth_yljg_retail_v1') }
  catch { timedScope.value = null; errs.push('授权读取失败') }
  try { dailyReports.value = await api.coreGet('/api/v1/drug-trace/daily-reports?limit=30') || [] }
  catch { dailyReports.value = []; errs.push('日报读取失败') }
  try {
    const s = await api.coreGet('/api/v1/drug-trace/upload-stats?days=' + statsDaysParam.value)
    statsDays.value = s?.days || []
  } catch { statsDays.value = []; errs.push('统计读取失败') }
  try {
    problemSubs.value = await api.coreGet('/api/v1/drug-trace/submission-outcomes?days=' + outcomesDays.value + '&only=problem') || []
  } catch { problemSubs.value = []; errs.push('异常提交读取失败') }
  timedError.value = errs.join('；')
  dailyDetail.value = null
  detailDate.value = ''
}
async function showDailyDetail(dateStr: string) {
  if (detailDate.value === dateStr) { detailDate.value = ''; dailyDetail.value = null; return }
  try {
    const rows = await api.coreGet('/api/v1/drug-trace/daily-reports/' + dateStr)
    // 同日多源（timer/manual）时优先展示 timer 行
    dailyDetail.value = (rows || []).find((r: any) => r.source === 'timer') || (rows || [])[0] || null
    detailDate.value = dailyDetail.value ? dateStr : ''
  } catch (e: any) { msg.value = '日报详情读取失败：' + (e.data?.error?.message || e.message) }
}
async function queryProblem(s: any) {
  try {
    const out = await api.corePost(`/api/v1/drug-trace/submissions/${s.id}/query`, {})
    msg.value = '查询已排队（job=' + out.job_id + '）；稍后刷新定时上报面板查看结果。'
    setTimeout(loadTimed, 1500)
  } catch (e: any) { msg.value = '查询失败：' + (e.data?.error?.message || e.message) }
}

const readinessLayers = ref<Record<string, any>>({})
async function loadReadiness() {
  try {
    const out = await api.coreGet('/api/v1/drug-trace/readiness')
    // FIN-06：新响应 {profiles, layers}；兼容旧列表形状
    if (Array.isArray(out)) { readiness.value = out; readinessLayers.value = {} }
    else { readiness.value = out.profiles || []; readinessLayers.value = out.layers || {} }
    readinessError.value = false
  } catch (e: any) {
    msg.value = '就绪读取失败：' + e.message
    readinessError.value = true
  } finally {
    readinessLoaded.value = true
  }
}
async function loadConnectors() {
  try {
    const list = await api.coreGet('/api/v1/connectors')
    connectors.value = (list || []).filter((c: any) => c.direction === 'source')
    if (!connectorId.value && connectors.value.length) connectorId.value = connectors.value[0].id
  } catch { connectors.value = [] }
}
async function loadRuns() {
  try { runs.value = await api.coreGet('/api/v1/drug-trace/collection-runs') } catch { runs.value = [] }
}
async function loadBills() {
  const p = new URLSearchParams()
  if (qPharmacy.value) p.set('pharmacy_code', qPharmacy.value)
  if (qKind.value) p.set('business_kind', qKind.value)
  try { bills.value = await api.coreGet('/api/v1/drug-trace/bills?' + p.toString()) } catch { bills.value = [] }
}
async function loadSubs() {
  try {
    const all = await api.coreGet('/api/v1/submissions?limit=100')
    subs.value = Array.isArray(all) ? all : all.items || []
  } catch { subs.value = [] }
}
async function loadRecon() {
  try {
    const list = await api.coreGet('/api/v1/reconciliations?limit=10')
    const arr = Array.isArray(list) ? list : list.items || []
    recon.value = arr.find((r: any) => r.scope?.domain === 'drug_trace') || null
    reconItems.value = []
    if (recon.value) {
      const d = await api.coreGet('/api/v1/drug-trace/reconciliation-runs/' + recon.value.id)
      reconItems.value = d.items || []
    }
  } catch { recon.value = null }
}

async function runCollect() {
  msg.value = ''
  try {
    const out = await api.corePost('/api/v1/drug-trace/collection-runs',
      { connector_id: connectorId.value, window_begin: winBegin.value, window_end: winEnd.value, mode: 'synthetic' })
    msg.value = '采集已排队（job=' + out.job_id + '）；稍后刷新运行/单据。'
    setTimeout(() => { loadRuns(); loadBills() }, 1500)
  } catch (e: any) { msg.value = '采集失败：' + (e.data?.error?.message || e.message) }
}
async function preview(b: any) {
  try { previewOut.value = await api.corePost('/api/v1/drug-trace/previews', { revision_id: currentRev(b) }) }
  catch (e: any) { previewOut.value = { error: e.data?.error?.message || e.message } }
}
function currentRev(b: any): string {
  // bills 列表未带 revision_id；从详情取最新
  return b.current_revision_id || ''
}
async function showCodes(b: any) {
  const d = await api.coreGet('/api/v1/drug-trace/bills/' + b.id)
  const rev = (d.revisions || [])[0]
  codesFor.value = { bill: b.source_bill_no, rev: rev?.id }
  codes.value = rev ? await api.coreGet(`/api/v1/drug-trace/revisions/${rev.id}/codes?limit=200`) : []
}
async function prepare(b: any) {
  msg.value = ''
  try {
    const d = await api.coreGet('/api/v1/drug-trace/bills/' + b.id)
    const rev = (d.revisions || [])[0]
    const profile = d.profile || 'alihealth_yljg_retail_v1'
    const out = await api.corePost(`/api/v1/drug-trace/bills/${b.id}/prepare`,
      { profile, destination_id: await destFor(profile) })
    msg.value = '提交已准备：' + out.submission_id + '（状态 ' + out.state + '；未发送）'
    loadSubs()
  } catch (e: any) { msg.value = '准备失败：' + (e.data?.error?.message || e.message) }
}
async function destFor(profile: string): Promise<string> {
  // 目的地按业务 profile 精确匹配（R2-15：零售单不会撞到库存 profile 目的地）
  await loadReadiness()
  const r = readiness.value.find((x: any) => x.profile === profile && x.destination?.id)
  return r?.destination?.id || ''
}
async function send(s: any) {
  msg.value = ''
  try {
    const out = await api.corePost(`/api/v1/drug-trace/submissions/${s.id}/send`,
      { confirm: true, reason: 'UI 合成闭环' }, 'ui-send-' + s.id)
    msg.value = '发送已批准并排队（job=' + out.job_id + '）；刷新查看状态。'
    setTimeout(loadSubs, 1500)
  } catch (e: any) { msg.value = '发送被拒：' + (e.data?.error?.message || e.message) }
}
async function query(s: any) {
  try {
    const out = await api.corePost(`/api/v1/drug-trace/submissions/${s.id}/query`, {})
    msg.value = '查询已排队（job=' + out.job_id + '）；刷新查看状态。'
    setTimeout(loadSubs, 1500)
  } catch (e: any) { msg.value = '查询失败：' + (e.data?.error?.message || e.message) }
}
async function runRecon() {
  msg.value = ''
  try {
    const dest = await destFor(reconProfile.value)
    const out = await api.corePost('/api/v1/drug-trace/reconciliation-runs',
      { profile: reconProfile.value, destination_id: dest })
    msg.value = '对账已排队（job=' + out.job_id + '）'
    setTimeout(loadRecon, 1500)
  } catch (e: any) { msg.value = '对账被拒：' + (e.data?.error?.message || e.message) }
}
async function dispose(i: any, action: string) {
  const reason = (dispReason.value[i.id] || '').trim()
  if (!reason) { msg.value = '处置必须填写理由'; return }
  try {
    await api.corePost(`/api/v1/drug-trace/reconciliation-items/${i.id}/dispositions`,
      { action, reason })
    msg.value = '处置已记录（' + action + '，理由已审计）'
    loadRecon()
  } catch (e: any) { msg.value = '处置失败：' + (e.data?.error?.message || e.message) }
}
async function approveEvidence() {
  msg.value = ''
  try {
    const prof = readiness.value.find((r: any) => r.profile === evProfile.value)
    const cap = prof?.capabilities?.find((c: any) => c.capability === evCapability.value)
    const items = (cap?.missing || []).map((k: string) => ({ key: k, evidence_ref: 'ui-demo:' + k }))
    // 证据绑定该 profile 的目的地（无 destination_id 的证据对具体目标不生效）
    const dest = prof?.destination?.id || ''
    const body: any = { profile: evProfile.value, capability: evCapability.value, destination_id: dest, items }
    if (evCapability.value === 'send') {
      // FIX3-04：send 证据携带精确配置绑定 + 完整 EXT-12 scope（服务端强校验）
      const maxBills = parseInt(evMaxBills.value, 10)
      const pharmacies = evPharmacies.value.split(/[,，\s]+/).filter(Boolean)
      if (!evConfigRelease.value || !evWinBegin.value || !evWinEnd.value
        || !pharmacies.length || !(maxBills > 0)) {
        msg.value = 'send 证据需完整 scope：config_release + 窗口 + 药房白名单 + 配额'
        return
      }
      body.config_release_id = evConfigRelease.value
      body.scope = {
        window_begin: evWinBegin.value, window_end: evWinEnd.value,
        pharmacies, max_bills: maxBills
      }
    }
    const out = await api.corePost('/api/v1/drug-trace/evidence', body)
    msg.value = '证据状态：' + out.status + '（演示登记；正式须院方材料）'
    loadReadiness()
  } catch (e: any) { msg.value = '证据登记失败：' + (e.data?.error?.message || e.message) }
}
async function revokeAll() {
  try {
    const out2 = await api.coreGet('/api/v1/drug-trace/readiness')
    const list = Array.isArray(out2) ? out2 : (out2.profiles || [])
    for (const r of list) {
      for (const c of r.capabilities) {
        if (c.status === 'APPROVED' && c.evidence_id) {
          await api.corePost(`/api/v1/drug-trace/evidence/${c.evidence_id}/revoke`, {})
        }
      }
    }
    msg.value = '已撤销全部药品证据（正式停发）'
    loadReadiness()
  } catch (e: any) { msg.value = '撤销失败：' + (e.data?.error?.message || e.message) }
}

onMounted(() => { loadReadiness(); loadConnectors(); loadRuns(); loadBills(); loadSubs(); loadRecon(); loadTimed() })
</script>

<style scoped>
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.ok { color: #1d6b33; }
.warn { color: #8a6116; }
.err { color: #9c2b2b; }
</style>
