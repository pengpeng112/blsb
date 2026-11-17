"""Extend the existing synthetic browser review with report-stage cases."""
from pathlib import Path
HERE=Path(__file__).resolve().parent
s=(HERE.parent/'drug-trace-ui-clarity-20261026/browser_review.py').read_text(encoding='utf8')
old="elif path.endswith('/daily-reports'):data=[] if empty else [report,manual]"
new="elif path.endswith('/daily-reports'):data=[] if empty else [{k:v for k,v in r.items() if k not in ('collection','send','query_stats')} for r in (report,manual)]"
assert s.count(old)==1;s=s.replace(old,new)
s=s.replace("'send':{'sent':567,", "'send':{'sent':report['sent'],")
s=s.replace("'send':{'sent':815,'skipped_terminal':222}","'send':{'sent':manual['sent'],'skipped_terminal':manual.get('skipped_terminal',222)}")
extra='''        report={**report,'outcome':'FAILED','candidate_count':0,'sent':0,'errors_count':0,
            'collection':{'id':'SYNTHETIC-RUN','state':'RUNNING','error_code':'collect_timeout','candidate_count_known':False,
                          'summary':{'bills_seen':3337,'valid':1995,'waiting':1342}}}
        manual={**manual,'outcome':'OK','candidate_count':1995,'sent':976,'skipped_terminal':1019,
            'collection':{'state':'DONE','summary':{'bills_seen':3337,'valid':1995,'waiting':1342}}}
        ctx=context();page=ctx.new_page();stage_errors=[];page.on('pageerror',lambda e:stage_errors.append(type(e).__name__))
        page.goto(base+'/ui/#/drugtrace',wait_until='networkidle')
        page.locator('.page-tabs button').filter(has_text='上报记录').click()
        assert page.get_by_text('待核实',exact=True).is_visible()
        result['checks']['failed_summary_candidate_is_unverified_not_zero']=True
        page.get_by_role('button',name='查看详情',exact=True).first.click()
        dialog=page.get_by_role('dialog');dialog.wait_for()
        dialog.get_by_text('未统计',exact=True).wait_for()
        assert dialog.get_by_text('等待采集完成超时',exact=True).is_visible()
        assert dialog.get_by_text('已采集3,337张，可上报1,995张，待完善1,342张',exact=True).is_visible()
        assert dialog.get_by_text('以上为当次任务结束时的采集回执。后台可能随后继续完成，请结合当前平台统计判断；未统计或待核实不代表零张。',exact=True).is_visible()
        page.add_style_tag(content='body::after{content:"界面预览 · 合成数据";position:fixed;bottom:10px;right:20px;background:white;padding:8px;z-index:1000}')
        page.screenshot(path=str(EVID/'report-collection-timeout.png'),full_page=True)
        result['checks']['timeout_reason_and_recorded_progress_visible_without_technical_json']=True
        page.get_by_role('button',name='关闭',exact=True).click()
        page.get_by_role('button',name='查看详情',exact=True).nth(1).click()
        dialog=page.get_by_role('dialog');dialog.wait_for()
        dialog.locator('.detail-summary').get_by_text('1,995',exact=True).wait_for()
        assert dialog.locator('.detail-summary').get_by_text('976',exact=True).is_visible()
        assert dialog.locator('.detail-summary').get_by_text('1,019',exact=True).is_visible()
        assert dialog.locator('section.notice').count()==0
        result['checks']['successful_recovery_keeps_per_run_sends_and_skips_separate']=True
        assert not stage_errors;ctx.close()
        report={**report,'collection':{}}
        ctx=context();page=ctx.new_page();page.goto(base+'/ui/#/drugtrace',wait_until='networkidle')
        page.locator('.page-tabs button').filter(has_text='上报记录').click()
        page.get_by_role('button',name='查看详情',exact=True).first.click()
        page.get_by_role('dialog').wait_for()
        assert page.get_by_role('dialog').get_by_text('失败回执缺少采集阶段详情，候选数量尚未核实',exact=True).is_visible()
        assert page.get_by_role('dialog').get_by_text('待核实',exact=True).is_visible()
        result['checks']['legacy_failed_receipt_does_not_invent_reason_or_zero']=True;ctx.close()
        report={**report,'collection':{'state':'DONE','summary':{'bills_seen':0,'valid':0,'waiting':0}}}
        ctx=context();page=ctx.new_page();page.goto(base+'/ui/#/drugtrace',wait_until='networkidle')
        page.locator('.page-tabs button').filter(has_text='上报记录').click()
        page.get_by_role('button',name='查看详情',exact=True).first.click()
        page.get_by_role('dialog').wait_for()
        assert page.get_by_role('dialog').locator('.detail-summary span').filter(has_text='候选单据').inner_text().strip().endswith('0')
        assert page.get_by_role('dialog').locator('section.notice').count()==0
        result['checks']['known_zero_after_completed_collection_stays_zero']=True;ctx.close()
'''
assert s.count('        browser.close()')==1
s=s.replace('        browser.close()',extra+'        browser.close()')
compile(s,'phase-browser','exec');(HERE/'phase_browser.py').write_text(s,encoding='utf8')
print('Prepared synthetic browser checks; no real connection')
