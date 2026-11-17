"""Apply the two report-template changes, without touching other UI behavior."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
p=ROOT/'implementation/apps/admin_web/src/views/DrugTrace.vue';s=p.read_text(encoding='utf8')
replacements={
 "import { label, number as n, totals, reportHealth } from './drugTraceDisplay'": "import { label, number as n, totals, reportHealth, candidateText, collectionFailure, collectionProgress } from './drugTraceDisplay'",
 '{{ n(r.candidate_count) }}':'{{ candidateText(r) }}',
 '{{ n(dailyDetail.candidate_count) }}':'{{ candidateText(dailyDetail) }}',
 '<th class="num">候选单据</th>':'<th class="num" title="失败记录未确认数量时显示待核实；不等于零张">候选单据</th>',
 '<div v-if="dailyDetail.send?.aborted_reason"': '<section v-if="collectionFailure(dailyDetail)" class="notice warning" role="status"><div><strong>{{ collectionFailure(dailyDetail) }}</strong><p v-if="collectionProgress(dailyDetail)">{{ collectionProgress(dailyDetail) }}</p><p class="panel-footnote">以上为当次任务结束时的采集回执。后台可能随后继续完成，请结合当前平台统计判断；未统计或待核实不代表零张。</p></div></section><div v-if="dailyDetail.send?.aborted_reason"',
}
for old,new in replacements.items():
    assert s.count(old)==1,old
    s=s.replace(old,new)
p.write_text(s,encoding='utf8')
print('Updated report candidate/collection presentation; no API/auth/send changes')
