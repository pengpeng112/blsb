"""One-time page transformation from the immutable baseline; never deploys."""
from pathlib import Path
import re
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
original=(HERE/'baselines/DrugTrace.vue').read_text('utf8')
def section(start,end):
    return original.split(start,1)[1].split(end,1)[0].strip()
ready=section('<!-- 就绪配置 -->','<!-- 定时上报（自动化） -->')
collect=section('<!-- 采集 -->','<!-- 单据 -->')
bills=section('<!-- 单据 -->','<!-- 提交/回执 -->')
recon=section('<!-- 对账 -->','<pre class="json" v-if="msg">')
ready=ready.replace('接入就绪（证据/缺项）','接入条件与授权明细')
ready=ready.replace('v-if="canApprove"','v-if="canApprove && retailDestination?.is_mock"')
ready=ready.replace("v-if=\"canApprove && evCapability === 'send'\"","v-if=\"canApprove && retailDestination?.is_mock && evCapability === 'send'\"")
ready=ready.replace('{{ c.capability }}','{{ label(c.capability) }}').replace('{{ c.status }}','{{ label(c.status) }}')
ready=ready.replace('（演示）','（仅演练）')
ready=re.sub(r'<button class="ghost" @click="revokeAll">.*?</button>','',ready)
collect=collect.replace('<div class="row">','<div class="row" v-if="canOperate && retailDestination?.is_mock">',1)
collect=collect.replace('{{ r.state }}','{{ label(r.state) }}')
bills=bills.replace('{{ k }}','{{ kindLabel(k) }}').replace('{{ b.business_kind }}','{{ kindLabel(b.business_kind) }}')
bills=bills.replace("{{ b.quality_state || '—' }}","{{ label(b.quality_state) }}")
bills=bills.replace('v-if="b.quality_state === \'VALID\'"','v-if="canOperate && b.quality_state === \'VALID\'"')
bills=bills.replace('预览(dry-run)','查看单据预览').replace('冻结/准备提交','准备提交')
recon=recon.replace('<button @click="runRecon">','<button v-if="canOperate" @click="runRecon">')
recon=recon.replace("v-if=\"i.disposition === 'PENDING'\"","v-if=\"canReview && i.disposition === 'PENDING'\"")
for name,value in [('READY_SECTION',ready),('COLLECT_SECTION',collect),('BILL_SECTION',bills),('RECON_SECTION',recon)]:
    value=re.sub(r'(<table\b[^>]*>.*?</table>)',r'<div class="table-scroll">\1</div>',value,flags=re.S)
    if name=='READY_SECTION':value='<details class="panel technical-details" open><summary>查看接入条件与授权明细</summary>'+value+'</details>'
    if name=='BILL_SECTION':value=value.replace('<h3>单据</h3>','<h3>业务单据</h3>')
    if name=='COLLECT_SECTION':value=value.replace('采集运行（合成视图演练）','最近采集记录')
    globals()[name]=value
template=(HERE/'page-template.txt').read_text('utf8')
template=template.replace('<option value="problem">需要处理</option>','<option value="problem">退回 / 部分成功</option>')
template=template.replace('<section class="panel scope-panel"><div class="panel-heading"><h3>发送授权</h3>', '<details class="panel scope-panel"><summary><h3>发送授权</h3>')
template=template.replace("{{ sectionOk.scope ? scopeApproved ? '有效授权' : '需要核查' : '暂不可用' }}</span></div>", "{{ sectionOk.scope ? scopeApproved ? '有效授权' : '需要核查' : '暂不可用' }}</span></summary><div class=\"scope-content\">")
template=template.replace('查看接入与授权 →</button></section>', '查看接入与授权 →</button></div></details>')
template=template.replace('@click="showDailyDetail(r.report_date)"','@click="showDailyDetail(r.report_date, r.source)"')
template=template.replace('<span>候选单据 <b>{{ n(dailyDetail.candidate_count) }}</b></span>', '<span>候选单据 <b>{{ n(dailyDetail.candidate_count) }}</b></span><span>已提交而跳过 <b>{{ n(dailyDetail.send?.skipped_terminal || 0) }}</b></span>')
for name in ['READY_SECTION','COLLECT_SECTION','BILL_SECTION','RECON_SECTION']:
    template=template.replace('<!-- '+name+' -->',globals()[name])
script=original.split('<script setup lang="ts">',1)[1].split('</script>',1)[0]
script=script.replace("import { ref, computed, onMounted } from 'vue'","import { ref, computed, onMounted, onUnmounted } from 'vue'\nimport { label, number as n, totals, reportHealth } from './drugTraceDisplay'")
start=script.index('const health = computed(');end=script.index('// 面板错误提示',start)
script=script[:start]+"const health = computed(() => reportHealth(dailyReports.value, clock.value))\n\n"+script[end:]
start=script.index('async function loadTimed()');end=script.index('async function showDailyDetail',start)
new='''async function loadTimed() {
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
'''
script=script[:start]+new+script[end:]
script=script.replace("onMounted(() => { loadReadiness(); loadConnectors(); loadRuns(); loadBills(); loadSubs(); loadRecon(); loadTimed() })",(HERE/'page-script-extra.txt').read_text('utf8'))
script=script.replace("if (!readinessLoaded.value) return '加载中…'","if (!readinessLoaded.value) return '正在读取接入配置'")
script=script.replace("return d.status === 'ENABLED' ? '正式链路'", "return d.status === 'ENABLED' ? '正式接口已配置'")
script=script.replace("msg.value = '查询已排队（job=' + out.job_id + '）；稍后刷新定时上报面板查看结果。'", "msg.value = '已提交状态查询，请稍后刷新查看；没有重新发送该单据。'\n    if (activeTab.value === 'reports') setTimeout(loadRecordRows, 1500)")
script=script.replace("msg.value = '发送已批准并排队（job=' + out.job_id + '）；刷新查看状态。'", "msg.value = '发送请求已排队，实际结果请在上报记录中确认。'\n    setTimeout(loadRecordRows, 1500)")
script=script.replace("async function send(s: any) {\n  msg.value = ''", "async function send(s: any) {\n  if (!canOperate.value || !confirm('确认发送这笔已准备的单据？发送后请通过平台结果确认是否成功。')) return\n  msg.value = ''")
script=script.replace("reason: 'UI 合成闭环'", "reason: '页面人工确认发送'")
script=script.replace('    loadSubs()','    loadRecordRows()')
script=script.replace('    setTimeout(loadSubs, 1500)','')
script=script.replace('async function showDailyDetail(dateStr: string)', "async function showDailyDetail(dateStr: string, source = '')")
script=script.replace("if (detailDate.value === dateStr) {", "if (detailDate.value === dateStr && (!source || dailyDetail.value?.source === source)) {")
script=script.replace("(rows || []).find((r: any) => r.source === 'timer')", "(rows || []).find((r: any) => r.source === (source || 'timer'))")
script=script.replace("const subs = ref<any[]>([])\n",'')
for start_marker,end_marker in [
    ('const modeNote = computed(', '// ---- 定时上报面板'),
    ('function stateClass(', 'function shortDate'),
    ('function trunc200(', '// 健康规则'),
    ('async function loadSubs()', 'async function loadRecon'),
]:
    a=script.index(start_marker);b=script.index(end_marker,a)
    script=script[:a]+script[b:]
script=script.replace('async function revokeAll() {\n  try {', "async function revokeAll() {\n  if (!confirm('确认撤销全部药品上报证据并停止真实上报？')) return\n  try {")
script=script.replace('async function approveEvidence() {\n  msg.value',"async function approveEvidence() {\n  if (!retailDestination.value?.is_mock) { msg.value = '演练证据登记仅限合成环境'; return }\n  msg.value")
(ROOT/'implementation/apps/admin_web/src/views/DrugTrace.vue').write_text(template+'\n\n<script setup lang="ts">'+script+'\n</script>\n\n<style scoped src="./drugTrace.css"></style>\n',encoding='utf8')
print('Rebuilt DrugTrace.vue from saved baseline; no backend changes')
