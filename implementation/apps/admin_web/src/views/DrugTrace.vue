<template>
  <div class="drugtrace-page">
    <header class="page-header">
      <div class="page-heading">
        <div class="page-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM15 14v3h-1v3h3v-2h3v-4M19 20h1" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" /></svg></div>
        <div><p class="eyebrow">码上放心 · 医疗机构数据上报</p><h2>药品追溯</h2><p class="page-description">看进度、查结果，集中处理需要关注的单据。</p></div>
      </div>
      <div class="header-tools">
        <span class="mode-badge" :class="modeClass"><i></i>{{ modeLabel }}</span>
        <button class="refresh-button" @click="refreshDashboard" :disabled="refreshing"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M16 8a6 6 0 1 0 .1 4M16 3v5h-5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>{{ refreshing ? '正在刷新' : '刷新数据' }}</button>
        <span class="last-refresh">{{ lastRefreshed ? '刷新于 ' + lastRefreshed : '正在读取数据' }}</span>
      </div>
    </header>

    <div v-if="retailDestination?.is_mock" class="notice info"><strong>当前为演练环境</strong><span>这里的合成结果不代表真实平台上报。</span></div>
    <div v-if="timedError" class="notice danger" role="alert"><strong>部分数据暂时无法读取</strong><span>{{ timedError }}。显示“—”的项目暂无有效统计，请刷新重试。</span></div>
    <div v-if="msg" class="action-message" role="status"><span>{{ msg }}</span><button class="text-button" @click="msg = ''" aria-label="关闭操作提示">关闭</button></div>

    <nav class="page-tabs" aria-label="药品追溯功能">
      <button v-for="tab in tabs" :key="tab.id" :class="{ active: activeTab === tab.id }" :aria-current="activeTab === tab.id ? 'page' : undefined" @click="switchTab(tab.id)">{{ tab.name }}<span v-if="tab.id === 'reports' && attentionCount" class="tab-count">{{ n(attentionCount) }}</span></button>
    </nav>

    <section v-if="activeTab === 'overview'" class="overview-pane">
      <div class="section-toolbar"><div><h3>上报总览</h3><p>药品追溯全部业务 · 按提交记录统计</p></div><div class="range-controls"><label class="auto-refresh"><input type="checkbox" v-model="autoRefresh" /> 每60秒刷新</label><select v-model.number="statsDaysParam" @change="changeRange" aria-label="统计范围"><option :value="7">近7天</option><option :value="14">近14天</option><option :value="30">近30天</option><option :value="90">近90天</option></select></div></div>
      <div class="metric-grid">
        <button v-for="card in metricCards" :key="card.state" class="metric-card" :class="card.tone" @click="openRecords(card.state)">
          <div class="metric-top"><span>{{ card.title }}</span><span class="metric-symbol" aria-hidden="true">{{ card.symbol }}</span></div>
          <div class="metric-value">{{ n(card.count) }}<small>笔</small></div><p>{{ card.note }}</p><span class="metric-link">查看记录 <span aria-hidden="true">↗</span></span>
        </button>
      </div>

      <div class="overview-grid">
        <div class="main-column">
          <section class="panel progress-panel">
            <div class="panel-heading"><div><h3>当前上报进度</h3><p>平台受理后，还需要查询确认最终结果。</p></div><span class="quiet-badge">共 {{ n(statsAvailable ? aggregate.total : null) }} 笔提交</span></div>
            <div class="progress-head"><strong>{{ summaryTitle }}</strong><span v-if="statsAvailable && aggregate.total">确认占比 <b>{{ confirmedPercent }}%</b></span></div>
            <div class="status-track" role="img" :aria-label="statsAvailable ? '已确认占比 ' + confirmedPercent + '%' : '统计暂不可用'">
              <span v-for="part in progressParts" :key="part.key" :class="part.tone" :style="{ width: part.percent + '%' }" :title="part.label + ' ' + n(part.value) + '笔'"></span>
            </div>
            <div class="track-legend"><span v-for="part in progressParts" :key="part.key"><i :class="part.tone"></i>{{ part.label }} <b>{{ n(part.value) }}</b></span></div>
            <div class="flow-grid"><div v-for="(step, i) in flowSteps" :key="step.name" class="flow-step"><span class="step-index">{{ i + 1 }}</span><span>{{ step.name }}</span><strong>{{ n(step.count) }}</strong><small>{{ step.note }}</small></div></div>
            <p class="panel-footnote">这是各状态的当前数量，不是同一批单据的累计流转量。结果待核查、部分成功及退回记录单独处理。</p>
          </section>

          <section class="panel trend-panel">
            <div class="panel-heading"><div><h3>近期提交情况</h3><p>按提交创建日期（UTC）分组，结果会随平台查询更新。</p></div><button class="text-button" @click="switchTab('reports')">查看全部记录 →</button></div>
            <div v-if="!statsAvailable" class="empty-state">{{ timedBusy ? '正在读取统计…' : '统计暂不可用，请刷新重试' }}</div>
            <div v-else-if="!chartDays.length" class="empty-state"><span class="empty-symbol">▤</span>近 {{ statsDaysParam }} 天暂无提交记录</div>
            <div v-else class="chart" role="img" aria-label="近期每日提交数量和状态">
              <div v-for="day in chartDays" :key="day.date" class="chart-day"><span class="chart-number">{{ n(day.total) }}</span><div class="chart-bar-area"><div class="chart-bar" :style="{ height: (day.total / chartMax * 110) + 'px' }"><span v-for="part in dayParts(day)" :key="part.key" :class="part.tone" :style="{ height: part.percent + '%' }" :title="day.date + ' ' + part.label + ' ' + n(part.value)"></span></div></div><span class="chart-date">{{ day.date.slice(5) }}</span></div>
            </div>
            <div class="chart-key"><span><i class="success"></i>确认成功</span><span><i class="pending"></i>平台受理</span><span><i class="unknown"></i>待核查</span><span><i class="failed"></i>部分成功 / 退回</span><span><i class="other"></i>其他</span></div>
          </section>
        </div>

        <aside class="summary-column">
          <section class="panel attention-panel"><div class="panel-heading"><h3>需要关注</h3><span v-if="attentionCount" class="count-pill">{{ n(attentionCount) }} 笔</span></div>
            <div v-if="!statsAvailable" class="empty-state compact">统计读取后显示待处理事项</div>
            <template v-else>
              <button v-if="count('UNKNOWN')" class="attention-item" @click="openRecords('UNKNOWN')"><i class="attention-dot unknown"></i><div><strong>结果待核查 <b>{{ n(count('UNKNOWN')) }}</b></strong><p>先核对平台结果，避免重复上报。</p></div><span>›</span></button>
              <button v-if="failedCount" class="attention-item" @click="openRecords('problem')"><i class="attention-dot failed"></i><div><strong>退回或部分成功 <b>{{ n(failedCount) }}</b></strong><p>查看返回原因，核对原始单据。</p></div><span>›</span></button>
              <button v-if="count('ACCEPTED')" class="attention-item" @click="openRecords('ACCEPTED')"><i class="attention-dot pending"></i><div><strong>等待最终确认 <b>{{ n(count('ACCEPTED')) }}</b></strong><p>平台已受理，尚未确认全部成功。</p></div><span>›</span></button>
              <p v-if="!attentionCount && !count('ACCEPTED')" class="empty-state compact">当前汇总未见待核查或平台退回记录。</p>
            </template>
            <div v-if="problemMismatch" class="inline-warning">汇总仍有待处理记录，近期明细暂未完整显示。请以汇总为准，不要据空列表判定已处理完。</div>
          </section>

          <section class="panel schedule-panel"><div class="panel-heading"><h3>定时上报</h3><span class="quiet-badge">每日 06:10</span></div><div class="schedule-next"><span>下次计划时间 · 北京时间</span><strong>{{ health.nextAt }}</strong></div>
            <dl class="simple-list"><div><dt>处理范围</dt><dd>前一日业务数据</dd></div><div><dt>最近日报</dt><dd>{{ sectionOk.reports ? health.latest?.report_date || '暂无记录' : '暂不可用' }}</dd></div><div><dt>当次结果</dt><dd><span v-if="health.latest && sectionOk.reports" class="status-badge" :class="health.latest.outcome === 'OK' ? 'success' : 'failed'">{{ label(health.latest.outcome) }}</span><span v-else>—</span></dd></div></dl>
            <p v-if="health.missing" class="inline-warning">{{ health.yesterdayStr }} 的日报尚未入库，需检查任务进度。</p><p class="panel-footnote">计划时间不代表任务当前运行状态；日报在任务结束后更新，可能晚于实时平台结果。</p><button class="secondary-button full-width" @click="switchTab('reports')">查看任务日报</button>
          </section>

          <details class="panel scope-panel"><summary><h3>发送授权</h3><span class="status-badge" :class="scopeApproved ? 'success' : 'neutral'">{{ sectionOk.scope ? scopeApproved ? '有效授权' : '需要核查' : '暂不可用' }}</span></summary><div class="scope-content"><template v-if="timedScope?.quota"><div class="quota-line"><strong>{{ n(timedScope.quota.used) }}</strong><span>/ {{ n(timedScope.quota.max_bills) }} 笔额度</span></div><div class="quota-track"><span :style="{ width: quotaPercent + '%' }"></span></div></template><dl class="simple-list"><div><dt>授权药房</dt><dd>{{ timedScope?.scope?.pharmacies?.length ?? '—' }} 家</dd></div><div><dt>业务窗口</dt><dd>{{ shortDate(timedScope?.scope?.window_begin) }} 至 {{ shortDate(timedScope?.scope?.window_end) }}</dd></div></dl><button class="text-button" @click="switchTab('settings')">查看接入与授权 →</button></div></details>
        </aside>
      </div>
      <p class="page-footnote">统计单位为“提交记录”，近 {{ statsDaysParam }} 天、全部药品追溯业务；日报按业务日期（北京时间），平台统计按提交创建日（UTC），两者不可按同日直接相减。</p>
    </section>

    <section v-if="activeTab === 'reports'" class="records-pane">
      <div class="section-toolbar"><div><h3>上报记录</h3><p>查看平台处理结果，按状态快速筛选。</p></div><button class="secondary-button" @click="loadRecordRows">刷新记录</button></div>
      <section class="panel"><div class="record-filters"><input v-model="recordSearch" placeholder="搜索单据号、平台单号或提交编号" aria-label="搜索上报记录" /><select v-model="recordState" aria-label="上报状态"><option value="">全部状态</option><option value="problem">退回 / 部分成功</option><option v-for="state in ['CONFIRMED','ACCEPTED','UNKNOWN','PARTIAL','REJECTED','PREPARED','SENDING']" :key="state" :value="state">{{ label(state) }}</option></select><span class="muted">已加载 {{ n(recordRows.length) }} 笔，当前显示 {{ n(filteredRecords.length) }} 笔</span></div>
        <p class="panel-footnote">明细最多显示近期200笔，筛选仅作用于已加载记录；完整数量请看总览汇总。查询状态不会重新发送单据。</p>
        <div v-if="recordError" class="notice danger" role="alert">{{ recordError }}</div><div v-if="recordIncomplete" class="notice warning">汇总仍有 {{ n(attentionCount) }} 笔需要关注，当前明细未完整覆盖。请通过平台核对名单，不能据此判定已处理完。</div>
        <div class="table-scroll"><table v-if="filteredRecords.length"><thead><tr><th>业务单据 / 平台单号</th><th>平台状态</th><th>最近反馈</th><th>更新时间（UTC）</th><th class="right">操作</th></tr></thead><tbody><tr v-for="s in filteredRecords" :key="s.id"><td><strong class="bill-number">{{ s.source_bill_no || s.bill_code || s.id.slice(0,8) }}</strong><small class="cell-secondary">{{ s.bill_code || s.id }}</small></td><td><span class="status-badge" :class="tone(s.state)">{{ label(s.state) }}</span></td><td>{{ s.attempt_http_status ? 'HTTP ' + s.attempt_http_status : '暂无发送反馈' }}<small class="cell-secondary">{{ label(s.attempt_outcome) }}</small></td><td class="mono">{{ (s.updated_at || '').slice(0,16).replace('T',' ') }}</td><td class="right"><button v-if="canOperate && ['ACCEPTED','UNKNOWN','PARTIAL','REJECTED'].includes(s.state)" class="text-button" @click="queryProblem(s)">查询结果</button><button v-if="canOperate && s.state === 'PREPARED'" class="text-button" @click="send(s)">发送单据</button><span v-if="s.state === 'CONFIRMED'" class="cell-secondary">已完成</span></td></tr></tbody></table><div v-else-if="!recordError" class="empty-state">{{ recordBusy ? '正在读取记录…' : '当前已加载记录中没有符合条件的提交' }}</div></div>
      </section>
      <section class="panel"><div class="panel-heading"><div><h3>任务日报</h3><p>记录任务当次执行结果；发送请求数与最终成功数分别统计。</p></div><span class="quiet-badge">业务日期 · 北京时间</span></div><div class="table-scroll"><table v-if="dailyReports.length"><thead><tr><th>业务日期</th><th>来源</th><th class="num" title="失败记录未确认数量时显示待核实；不等于零张">候选单据</th><th class="num">发送请求</th><th class="num">准备 / 发送错误</th><th class="num">查询错误</th><th>当次结果</th><th>耗时</th><th></th></tr></thead><tbody><tr v-for="r in dailyReports" :key="r.report_date+r.source"><td class="mono">{{ r.report_date }}</td><td>{{ label(r.source) }}</td><td class="num">{{ candidateText(r) }}</td><td class="num">{{ n(r.sent) }}</td><td class="num" :class="r.errors_count ? 'error-text' : ''">{{ n(r.errors_count || 0) }}</td><td class="num">{{ n(r.query_errors_count || 0) }}</td><td><span class="status-badge" :class="r.outcome==='OK'?'success':'failed'">{{ label(r.outcome) }}</span></td><td>{{ fmtMinutes(r.elapsed_seconds) }}</td><td><button class="text-button" @click="showDailyDetail(r.report_date, r.source)">查看详情</button></td></tr></tbody></table><div v-else class="empty-state">{{ sectionOk.reports ? '暂无任务日报' : '日报暂不可用，请刷新重试' }}</div></div></section>
    </section>

    <section v-if="activeTab === 'bills'" class="tool-pane"><div class="section-toolbar"><div><h3>单据与追溯码</h3><p>核查业务分类、药房、单据完整性及追溯码明细。</p></div></div>
      <div class="card">
      <h3>业务单据</h3>
      <div class="row">
        <input v-model="qPharmacy" placeholder="药房代码过滤" style="width:130px" />
        <select v-model="qKind">
          <option value="">全部分类</option>
          <option v-for="k in kinds" :key="k" :value="k">{{ kindLabel(k) }}</option>
        </select>
        <button class="ghost" @click="loadBills">刷新</button>
      </div>
      <div class="table-scroll"><table v-if="bills.length">
        <tr><th>药房</th><th>分类</th><th>单号</th><th>来源版本</th><th>质量</th><th>码数</th><th>完成</th><th>业务时间</th><th>操作</th></tr>
        <tr v-for="b in bills" :key="b.id">
          <td>{{ b.pharmacy_code }}</td>
          <td>{{ kindLabel(b.business_kind) }}</td>
          <td class="mono">{{ b.source_bill_no }}</td>
          <td class="mono">{{ b.source_revision }}</td>
          <td><b :class="b.quality_state === 'VALID' ? 'ok' : (b.quality_state === 'WAITING' ? 'warn' : (b.quality_state === 'NOT_REPORTABLE' ? 'muted' : 'err'))">{{ label(b.quality_state) }}</b></td>
          <td>{{ b.code_count }}</td>
          <td>{{ b.finalized ? '是' : '否' }}</td>
          <td class="mono">{{ (b.bill_time || '').slice(0, 16) }}</td>
          <td>
            <button class="ghost" @click="preview(b)">查看单据预览</button>
            <button class="ghost" @click="showCodes(b)">码明细</button>
            <button v-if="canOperate && b.quality_state === 'VALID'" @click="prepare(b)">准备提交</button>
          </td>
        </tr>
      </table></div>
      <pre class="json" v-if="previewOut">{{ JSON.stringify(previewOut, null, 2) }}</pre>
      <div v-if="codesFor">
        <h4>码明细（{{ codesFor.bill }}，掩码）</h4>
        <div class="table-scroll"><table>
          <tr><th>扫码记录</th><th>明细行</th><th>码（掩码）</th><th>层级</th><th>数量</th><th>删除</th></tr>
          <tr v-for="c in codes" :key="c.source_code_record_id">
            <td class="mono">{{ c.source_code_record_id }}</td>
            <td class="mono">{{ c.source_line_id }}</td>
            <td class="mono">{{ c.trace_code_masked }}</td>
            <td>{{ c.code_level || '—' }}</td>
            <td>{{ c.quantity }}{{ c.unit }}</td>
            <td>{{ c.is_deleted ? '是' : '否' }}</td>
          </tr>
        </table></div>
      </div>
    </div>
      <details class="panel technical-details"><summary>数据采集记录与演练工具</summary><p class="panel-footnote">这里的手动采集仅为合成演练；正式数据由已部署任务抽取。</p><div class="card">
      <h3>最近采集记录</h3>
      <div class="row" v-if="canOperate && retailDestination?.is_mock">
        <select v-model="connectorId"><option v-for="c in connectors" :key="c.id" :value="c.id">{{ c.plugin_id }} / {{ c.name }}</option></select>
        <input v-model="winBegin" placeholder="2026-10-12T00:00:00" style="width:200px" />
        <input v-model="winEnd" placeholder="2026-10-13T00:00:00" style="width:200px" />
        <button @click="runCollect" :disabled="!connectorId">发起采集（半开窗口）</button>
        <button class="ghost" @click="loadRuns">刷新运行</button>
      </div>
      <div class="table-scroll"><table v-if="runs.length">
        <tr><th>运行</th><th>状态</th><th>窗口</th><th>摘要</th></tr>
        <tr v-for="r in runs" :key="r.id">
          <td class="mono">{{ r.id.slice(0, 8) }}</td>
          <td>{{ label(r.state) }}</td>
          <td class="mono">{{ (r.window[0] || '').slice(0, 16) }} → {{ (r.window[1] || '').slice(0, 16) }}</td>
          <td class="mono">{{ JSON.stringify(r.summary) }}</td>
        </tr>
      </table></div>
    </div></details>
    </section>

    <section v-if="activeTab === 'reconcile'" class="tool-pane"><div class="section-toolbar"><div><h3>对账核查</h3><p>核对本地与平台单据状态，查看差异及已有处置记录。</p></div></div><div class="card">
      <h3>对账（单据级；码级待 EXT-09）</h3>
      <div class="row">
        <select v-model="reconProfile"><option v-for="p in profileKeys" :key="p" :value="p">{{ p }}</option></select>
        <button v-if="canOperate" @click="runRecon">发起对账</button>
        <button class="ghost" @click="loadRecon">刷新最近对账</button>
      </div>
      <div class="table-scroll"><table v-if="recon">
        <tr><th>Run</th><th>状态</th><th>摘要</th><th>平台快照</th><th>码级核验</th></tr>
        <tr><td class="mono">{{ recon.id.slice(0, 8) }}</td>
            <td><b :class="recon.state === 'COMPLETE' ? 'ok' : 'warn'">{{ recon.state }}</b></td>
            <td class="mono">{{ JSON.stringify(recon.summary) }}</td>
            <td>{{ recon.scope?.platform_ok === false ? '不可用（未核验：' + (recon.scope?.remote_error || '') + '）' : '可用' }}</td>
            <td>{{ recon.scope?.code_level_verified ? '已核验' : '未核验（EXT-09）' }}</td></tr>
      </table></div>
      <div class="table-scroll"><table v-if="reconItems.length">
        <tr><th>单据</th><th>期望</th><th>实际</th><th>差异类型</th><th>处置</th><th>操作</th></tr>
        <tr v-for="i in reconItems" :key="i.id">
          <td class="mono">{{ i.item_key }}</td><td>{{ i.expected_state }}</td>
          <td>{{ i.actual_state }}</td><td>{{ i.detail?.kind }}</td>
          <td>{{ i.disposition }}</td>
          <td>
            <template v-if="canReview && i.disposition === 'PENDING'">
              <input v-model="dispReason[i.id]" placeholder="处置理由（必填）" style="width:150px" />
              <button @click="dispose(i, 'COMPENSATE')">批准补偿</button>
              <button class="ghost" @click="dispose(i, 'IGNORE')">忽略</button>
            </template>
          </td>
        </tr>
      </table></div>
    </div></section>

    <section v-if="activeTab === 'settings'" class="tool-pane"><div class="section-toolbar"><div><h3>接入与授权</h3><p>低频配置集中在此处，日常查看上报进度请使用“总览”。</p></div></div><div class="notice info">就绪配置用于核对接入条件；“网络状态未知”不能视为已经连通。</div><details class="panel technical-details" open><summary>查看接入条件与授权明细</summary><div class="card">
      <h3>接入条件与授权明细</h3>
      <button class="ghost" @click="loadReadiness">刷新就绪状态</button>
      <p v-if="Object.keys(readinessLayers).length" class="row small">
        分层：<template v-for="(v, k) in readinessLayers" :key="k">
          <b :class="v.status === 'READY' ? 'ok' : (v.status === 'UNKNOWN' ? 'warn' : 'err')">{{ k }}={{ v.status }}</b>&nbsp;
        </template>
        <span class="mono small">（NETWORK=UNKNOWN 不是绿灯：需现场实测 <prod-host>→DMZ443 与 DMZ→平台）</span>
      </p>
      <div class="table-scroll"><table v-if="readiness.length">
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
            <td>{{ label(c.capability) }}</td>
            <td><b :class="c.status === 'APPROVED' ? 'ok' : 'warn'">{{ label(c.status) }}</b></td>
            <td>{{ (c.missing || []).join(', ') || '—' }}</td>
            <td>{{ r.signature_verified ? '是' : '否（EXT-07）' }}</td>
          </tr>
        </template>
      </table></div>
      <div class="row" v-if="canApprove && retailDestination?.is_mock">
        <h4>登记证据（platform_admin）</h4>
        <select v-model="evProfile"><option v-for="p in profileKeys" :key="p" :value="p">{{ p }}</option></select>
        <select v-model="evCapability"><option>collect</option><option>send</option><option>query</option><option>reconcile</option></select>
        <button @click="approveEvidence">按当前缺项登记全部证据（仅演练）</button>
        
      </div>
      <div class="row" v-if="canApprove && retailDestination?.is_mock && evCapability === 'send'">
        <!-- FIX3-04：send 证据必须精确绑定 config_release 与完整 EXT-12 scope
             （窗口/药房白名单/配额；空值不是通配） -->
        <input v-model="evConfigRelease" placeholder="config_release_id（send 必填，精确绑定）" style="width:280px" />
        <input v-model="evWinBegin" placeholder="授权窗口起 2026-10-01T00:00:00" style="width:200px" />
        <input v-model="evWinEnd" placeholder="授权窗口止 2026-11-01T00:00:00" style="width:200px" />
        <input v-model="evPharmacies" placeholder="药房白名单（逗号分隔，显式枚举）" style="width:220px" />
        <input v-model="evMaxBills" placeholder="配额 max_bills" style="width:120px" />
      </div>
    </div></details><details v-if="canApprove" class="panel technical-details"><summary>停发操作</summary><p class="panel-footnote">撤销全部证据后，真实采集、发送、查询与对账均会被拦截。此操作需要再次确认。</p><button class="secondary-button stop-button" @click="revokeAll">撤销上报证据并停发</button></details></section>

    <div v-if="dailyDetail" class="detail-overlay" role="dialog" aria-modal="true" aria-labelledby="daily-detail-title" @click.self="dailyDetail=null; detailDate=''">
      <section class="detail-dialog"><div class="panel-heading"><div><p class="eyebrow">任务执行记录 · 不代表当前批次进度</p><h3 id="daily-detail-title">{{ dailyDetail.report_date }} 日报详情</h3></div><button class="secondary-button" @click="dailyDetail=null; detailDate=''">关闭</button></div><div class="detail-summary"><span>当次结果 <b>{{ label(dailyDetail.outcome) }}</b></span><span>发送请求 <b>{{ n(dailyDetail.send?.sent || 0) }}</b></span><span>候选单据 <b>{{ candidateText(dailyDetail) }}</b></span><span>已提交而跳过 <b>{{ n(dailyDetail.send?.skipped_terminal || 0) }}</b></span><span>耗时 <b>{{ fmtMinutes(dailyDetail.elapsed_seconds) }}</b></span></div><p class="panel-footnote">业务窗口 {{ dailyDetail.window?.[0] }} 至 {{ dailyDetail.window?.[1] }}（北京时间）。这是当次任务结束时的记录，处理问题后请结合最新平台统计判断。</p>
        <section v-if="collectionFailure(dailyDetail)" class="notice warning" role="status"><div><strong>{{ collectionFailure(dailyDetail) }}</strong><p v-if="collectionProgress(dailyDetail)">{{ collectionProgress(dailyDetail) }}</p><p class="panel-footnote">以上为当次任务结束时的采集回执。后台可能随后继续完成，请结合当前平台统计判断；未统计或待核实不代表零张。</p></div></section><div v-if="dailyDetail.send?.aborted_reason" class="notice warning">任务中止原因：{{ errorText({ error_code: dailyDetail.send.aborted_reason }) }}</div><h4>准备与发送问题</h4><p v-if="dailyDetail.send?.errors_truncated" class="panel-footnote">共 {{ n(dailyDetail.send.errors_total) }} 项，显示已保留的前 {{ dailyDetail.send.errors.length }} 项。</p><div class="table-scroll"><table v-if="dailyDetail.send?.errors?.length"><thead><tr><th>单据 / 提交编号</th><th>步骤</th><th>问题说明</th></tr></thead><tbody><tr v-for="(e,i) in dailyDetail.send.errors" :key="i"><td class="mono">{{ e.bill || e.submission || '—' }}</td><td>{{ stepLabel(e.step) }}</td><td>{{ errorText(e.res || e) }}</td></tr></tbody></table><p v-else class="empty-state compact">本次记录未列出准备或发送错误。</p></div><h4>查询与后续核查</h4><p class="panel-footnote">查询错误 {{ n(dailyDetail.query_stats?.errors_total || dailyDetail.query_stats?.errors?.length || 0) }} 项；查询轮次已用尽 {{ n(dailyDetail.query_stats?.skipped_exhausted || 0) }} 笔；已有查询在途 {{ n(dailyDetail.query_stats?.skipped_inflight || 0) }} 笔。</p><div class="table-scroll"><table v-if="dailyDetail.query_stats?.errors?.length"><thead><tr><th>提交编号</th><th>问题说明</th></tr></thead><tbody><tr v-for="(e,i) in dailyDetail.query_stats.errors" :key="i"><td class="mono">{{ e.submission }}</td><td>{{ errorText(e.res || e) }}</td></tr></tbody></table></div><details class="technical-details"><summary>查看完整技术信息</summary><pre class="json">{{ JSON.stringify(dailyDetail,null,2) }}</pre></details>
      </section>
    </div>
  </div>
</template>


<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { label, number as n, totals, reportHealth, candidateText, collectionFailure, collectionProgress } from './drugTraceDisplay'
import { api } from '../api'

const profileKeys = ['alihealth_yljg_inout_v1', 'alihealth_yljg_retail_v1']
const kinds = ['SUPPLIER_IN', 'SUPPLIER_RETURN', 'PATIENT_DISPENSE', 'PATIENT_RETURN', 'INPATIENT_DISPENSE', 'UNKNOWN']

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
  if (!readinessLoaded.value) return '正在读取接入配置'
  if (readinessError.value) return '未知（就绪读取失败）'
  const d = retailDestination.value
  if (!d) return '未启用（未绑定目的地）'
  if (d.is_mock) return 'MOCK/合成'
  return d.status === 'ENABLED' ? '正式接口已配置' : `未启用（${d.status}）`
})
const modeClass = computed(() => {
  if (!readinessLoaded.value) return 'muted'
  if (readinessError.value) return 'warn'
  const d = retailDestination.value
  return d && !d.is_mock && d.status === 'ENABLED' ? 'ok' : 'warn'
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

function shortDate(v?: string) {
  return (v || '').slice(0, 10) || '—'
}
function fmtMinutes(sec?: number) {
  if (!sec || sec <= 0) return '—'
  return sec >= 90 ? Math.round(sec / 60) + ' 分钟' : Math.round(sec) + ' 秒'
}
// 健康规则（任务书 §5.4）：纯数据推导，不查 systemd
const health = computed(() => reportHealth(dailyReports.value, clock.value))

// 面板错误提示（FIX-06）：不静默吞错——各段失败显示对应文案（「；」连接）
const timedError = ref('')
async function loadTimed() {
  if (timedBusy.value) return
  timedBusy.value = true
  try {
    const results = await Promise.allSettled([
      api.coreGet('/api/v1/drug-trace/send-scope?profile=alihealth_yljg_retail_v1'),
      api.coreGet('/api/v1/drug-trace/daily-reports?limit=30'),
      api.coreGet('/api/v1/drug-trace/upload-stats?days=' + statsDaysParam.value),
      api.coreGet('/api/v1/drug-trace/submission-outcomes?days=' + outcomesDays.value + '&only=problem'),
    ])
    const keys = ['scope','reports','stats','outcomes'], messages = ['授权读取失败','日报读取失败','统计读取失败','明细读取失败']
    const errors: string[] = []
    results.forEach((result, i) => { sectionOk.value[keys[i]] = result.status === 'fulfilled'; if (result.status === 'rejected') errors.push(messages[i]) })
    timedScope.value = results[0].status === 'fulfilled' ? results[0].value : null
    dailyReports.value = results[1].status === 'fulfilled' ? results[1].value || [] : []
    statsDays.value = results[2].status === 'fulfilled' ? results[2].value?.days || [] : []
    problemSubs.value = results[3].status === 'fulfilled' ? results[3].value || [] : []
    timedError.value = errors.join('；')
  } finally { timedBusy.value = false }
}
async function showDailyDetail(dateStr: string, source = '') {
  if (detailDate.value === dateStr && (!source || dailyDetail.value?.source === source)) { detailDate.value = ''; dailyDetail.value = null; return }
  try {
    const rows = await api.coreGet('/api/v1/drug-trace/daily-reports/' + dateStr)
    // 同日多源（timer/manual）时优先展示 timer 行
    dailyDetail.value = (rows || []).find((r: any) => r.source === (source || 'timer')) || (rows || [])[0] || null
    detailDate.value = dailyDetail.value ? dateStr : ''
  } catch (e: any) { msg.value = '日报详情读取失败：' + (e.data?.error?.message || e.message) }
}
async function queryProblem(s: any) {
  try {
    const out = await api.corePost(`/api/v1/drug-trace/submissions/${s.id}/query`, {})
    msg.value = '已提交状态查询，请稍后刷新查看；没有重新发送该单据。'
    if (activeTab.value === 'reports') setTimeout(loadRecordRows, 1500)
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
    loadRecordRows()
  } catch (e: any) { msg.value = '准备失败：' + (e.data?.error?.message || e.message) }
}
async function destFor(profile: string): Promise<string> {
  // 目的地按业务 profile 精确匹配（R2-15：零售单不会撞到库存 profile 目的地）
  await loadReadiness()
  const r = readiness.value.find((x: any) => x.profile === profile && x.destination?.id)
  return r?.destination?.id || ''
}
async function send(s: any) {
  if (!canOperate.value || !confirm('确认发送这笔已准备的单据？发送后请通过平台结果确认是否成功。')) return
  msg.value = ''
  try {
    const out = await api.corePost(`/api/v1/drug-trace/submissions/${s.id}/send`,
      { confirm: true, reason: '页面人工确认发送' }, 'ui-send-' + s.id)
    msg.value = '发送请求已排队，实际结果请在上报记录中确认。'
    setTimeout(loadRecordRows, 1500)

  } catch (e: any) { msg.value = '发送被拒：' + (e.data?.error?.message || e.message) }
}
async function query(s: any) {
  try {
    const out = await api.corePost(`/api/v1/drug-trace/submissions/${s.id}/query`, {})
    msg.value = '查询已排队（job=' + out.job_id + '）；刷新查看状态。'

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
  if (!retailDestination.value?.is_mock) { msg.value = '演练证据登记仅限合成环境'; return }
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
  if (!confirm('确认撤销全部药品上报证据并停止真实上报？')) return
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

const tabs = [
  { id: 'overview', name: '总览' }, { id: 'reports', name: '上报记录' },
  { id: 'bills', name: '单据与追溯码' }, { id: 'reconcile', name: '对账核查' }, { id: 'settings', name: '接入与授权' },
]
const activeTab = ref('overview')
const autoRefresh = ref(true)
const refreshing = ref(false)
const timedBusy = ref(false)
const clock = ref(new Date())
const lastRefreshed = ref('')
const sectionOk = ref<Record<string, boolean>>({ scope: false, reports: false, stats: false, outcomes: false })
const recordRows = ref<any[]>([]), recordState = ref(''), recordSearch = ref('')
const recordBusy = ref(false), recordError = ref('')
const loadedTabs = new Set<string>()
const canReview = computed(() => roles.includes('business_reviewer') || canApprove.value)
const statsAvailable = computed(() => sectionOk.value.stats)
const aggregate = computed(() => totals(statsDays.value))
function count(state: string): number | null { return statsAvailable.value ? aggregate.value[state] || 0 : null }
const failedCount = computed(() => (count('PARTIAL') || 0) + (count('REJECTED') || 0))
const attentionCount = computed(() => statsAvailable.value ? (count('UNKNOWN') || 0) + failedCount.value : null)
const metricCards = computed(() => [
  { title: '已确认成功', state: 'CONFIRMED', count: count('CONFIRMED'), tone: 'success', symbol: '✓', note: '平台已确认处理成功' },
  { title: '平台已受理', state: 'ACCEPTED', count: count('ACCEPTED'), tone: 'pending', symbol: '◷', note: '已收到，等待最终确认' },
  { title: '结果待核查', state: 'UNKNOWN', count: count('UNKNOWN'), tone: 'unknown', symbol: '?', note: '先核查结果，不直接重发' },
  { title: '退回 / 部分成功', state: 'problem', count: statsAvailable.value ? failedCount.value : null, tone: 'failed', symbol: '!', note: '核对平台反馈与业务数据' },
])
const confirmedPercent = computed(() => statsAvailable.value && aggregate.value.total ? ((aggregate.value.CONFIRMED || 0) / aggregate.value.total * 100).toFixed(1) : '0.0')
const summaryTitle = computed(() => {
  if (!statsAvailable.value) return timedBusy.value ? '正在读取上报进度…' : '暂无法判断当前进度'
  if (!aggregate.value.total) return '所选时间范围内暂无提交'
  if (attentionCount.value) return n(attentionCount.value) + ' 笔记录需要关注'
  if (count('ACCEPTED')) return '已受理记录正在等待平台最终确认'
  if ((count('CONFIRMED') || 0) === aggregate.value.total) return '所选范围内的提交均已确认成功'
  return '请关注待发送及其他状态记录'
})
function parts(row: any) {
  const states = row.by_state || {}, total = Number(row.total || 0)
  const groups = [
    { key: 'CONFIRMED', label: '确认成功', tone: 'success', value: Number(states.CONFIRMED || 0) },
    { key: 'ACCEPTED', label: '平台受理', tone: 'pending', value: Number(states.ACCEPTED || 0) },
    { key: 'UNKNOWN', label: '待核查', tone: 'unknown', value: Number(states.UNKNOWN || 0) },
    { key: 'failed', label: '部分成功 / 退回', tone: 'failed', value: Number(states.PARTIAL || 0) + Number(states.REJECTED || 0) },
  ]
  groups.push({ key: 'other', label: '其他', tone: 'other', value: Math.max(0, total - groups.reduce((s, r) => s + r.value, 0)) })
  return groups.map(r => ({ ...r, percent: total ? r.value / total * 100 : 0 }))
}
function dayParts(day: any) { return parts(day) }
const progressParts = computed(() => statsAvailable.value ? parts({ total: aggregate.value.total, by_state: aggregate.value }) : [])
const chartDays = computed(() => statsDays.value.slice(0, 7).reverse())
const chartMax = computed(() => Math.max(1, ...chartDays.value.map(d => d.total || 0)))
const flowSteps = computed(() => [
  { name: '待发送', count: count('PREPARED'), note: '已准备好提交' },
  { name: '发送中', count: count('SENDING'), note: '等待接口返回' },
  { name: '平台已受理', count: count('ACCEPTED'), note: '等待最终确认' },
  { name: '已确认成功', count: count('CONFIRMED'), note: '平台处理完成' },
])
const scopeApproved = computed(() => timedScope.value?.evidence?.status === 'APPROVED')
const quotaPercent = computed(() => {
  const q = timedScope.value?.quota
  return q?.max_bills ? Math.min(100, q.used / q.max_bills * 100) : 0
})
const problemMismatch = computed(() => sectionOk.value.outcomes && !!attentionCount.value && problemSubs.value.length === 0)
const filteredRecords = computed(() => {
  const search = recordSearch.value.trim().toLocaleLowerCase()
  return recordRows.value.filter(s => {
    const stateMatches = !recordState.value || (recordState.value === 'problem'
      ? ['PARTIAL', 'REJECTED'].includes(s.state) : s.state === recordState.value)
    return stateMatches && (!search || [s.id, s.source_bill_no, s.bill_code].some(x => String(x || '').toLocaleLowerCase().includes(search)))
  })
})
const recordIncomplete = computed(() => !recordBusy.value && !recordError.value && statsAvailable.value && !!attentionCount.value
  && ['UNKNOWN','problem'].includes(recordState.value) && filteredRecords.value.length === 0)
function tone(state: string) {
  if (['CONFIRMED','VALID','OK','DONE','APPROVED'].includes(state)) return 'success'
  if (state === 'ACCEPTED' || state === 'SENDING') return 'pending'
  if (state === 'UNKNOWN') return 'unknown'
  if (['REJECTED','PARTIAL','FAILED','INVALID'].includes(state)) return 'failed'
  return 'neutral'
}
function kindLabel(kind: string) { return kind === 'UNKNOWN' ? '业务待确认' : label(kind) }
function stepLabel(step: string) { return ({ prepare: '准备单据', send: '发送请求', send_worker: '等待发送结果', state: '检查状态' } as Record<string,string>)[step] || step }
function errorText(value: any) {
  const v = value?.error || value || {}, code = v.error_code || v.code || ''
  const messages: Record<string,string> = {
    DICTIONARY_UNMAPPED: '药房、企业或业务字典尚未完成映射', SEND_QUEUE_TIMEOUT: '发送排队等待超时',
    SEND_EXECUTION_TIMEOUT: '接口执行等待超时', SEND_WORKER_TIMEOUT: '等待发送结果超时（旧版记录）',
    SEND_RESULT_UNKNOWN: '发送结果未知，请先核对平台', PROFILE_UNAPPROVED: '发送授权或授权范围需要核查',
    VERSION_CONFLICT: '状态或查询轮次已变化，需要核查', NOT_SENDABLE: '当前状态不允许发送',
  }
  return messages[code] || v.message || code || '请展开技术信息查看详细原因'
}
async function refreshDashboard() {
  if (refreshing.value) return
  refreshing.value = true
  try { await Promise.allSettled([loadReadiness(), loadTimed()]) }
  finally {
    clock.value = new Date()
    lastRefreshed.value = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(clock.value)
    refreshing.value = false
  }
}
async function changeRange() { outcomesDays.value = statsDaysParam.value; await refreshDashboard() }
async function loadRecordRows() {
  if (recordBusy.value) return
  recordBusy.value = true; recordError.value = ''
  try { recordRows.value = await api.coreGet('/api/v1/drug-trace/submission-outcomes?days=' + statsDaysParam.value) || [] }
  catch (e: any) { recordRows.value = []; recordError.value = '上报记录读取失败：' + (e.message || '请重试') }
  finally { recordBusy.value = false }
}
async function switchTab(id: string) {
  activeTab.value = id
  if (id === 'reports') { await loadRecordRows(); return }
  if (loadedTabs.has(id)) return
  loadedTabs.add(id)
  if (id === 'bills') await Promise.allSettled([loadConnectors(),loadRuns(),loadBills()])
  if (id === 'reconcile') await loadRecon()
  if (id === 'settings') await loadReadiness()
}
function openRecords(state: string) { recordState.value = state; void switchTab('reports') }
let refreshTimer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  outcomesDays.value = statsDaysParam.value
  void refreshDashboard()
  refreshTimer = setInterval(() => {
    clock.value = new Date()
    if (autoRefresh.value && document.visibilityState !== 'hidden' && ['overview','reports'].includes(activeTab.value)) {
      void refreshDashboard()
      if (activeTab.value === 'reports') void loadRecordRows()
    }
  }, 60_000)
})
onUnmounted(() => { if (refreshTimer) clearInterval(refreshTimer) })


</script>

<style scoped src="./drugTrace.css"></style>
