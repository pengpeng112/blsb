"""Visual and interaction checks with synthetic HTTP fixtures; no hospital access."""
from pathlib import Path
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json,threading,urllib.parse
from playwright.sync_api import sync_playwright

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];EVID=HERE/'evidence';EVID.mkdir(exist_ok=True)
DIST=ROOT/'implementation/apps/admin_web/dist'
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kw):super().__init__(*args,directory=str(DIST),**kw)
    def do_GET(self):
        if self.path.startswith('/ui/'):self.path=self.path[3:]
        super().do_GET()
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base='http://127.0.0.1:'+str(server.server_address[1])
days=[]
for day,success in [('2026-10-26',348),('2026-10-25',280),('2026-10-24',220),('2026-10-23',170),('2026-10-22',100),('2026-10-21',80),('2026-10-20',50)]:
    states={'CONFIRMED':success}
    if day=='2026-10-26':states.update(ACCEPTED=41,UNKNOWN=206,PARTIAL=2,REJECTED=3,PREPARED=24,SENDING=1)
    days.append({'date':day,'by_state':states,'total':sum(states.values()),'attempts':{'accepted':success,'http_error':0,'other':0}})
report={'report_date':'2026-10-25','source':'timer','outcome':'PARTIAL_FAILURE','candidate_count':1352,'sent':567,'errors_count':782,'query_errors_count':0,'query_skipped_exhausted':247,'elapsed_seconds':3610,'created_at':'2026-10-26T02:02:00'}
scope={'profile':'alihealth_yljg_retail_v1','evidence':{'evidence_id':'synthetic-evidence','status':'APPROVED','valid_until':'2026-11-21T16:00:00'},'scope':{'window_begin':'2026-10-24T00:00:00+08:00','window_end':'2026-11-23T00:00:00+08:00','pharmacies':['050102','050121','050116'],'max_bills':80000},'quota':{'used':1525,'max_bills':80000},'destination':{'id':'synthetic-dest','code':'synthetic-retail','status':'ENABLED','is_mock':False},'ambiguous':False}
manual={**report,'source':'manual','outcome':'OK','sent':815,'errors_count':0}
rows=[{'id':'synthetic-'+state,'state':state,'bill_code':'DEMO-'+str(i),'source_bill_no':'仅测试-'+str(i),'updated_at':'2026-10-26T02:05:00','created_at':'2026-10-26T01:00:00','attempt_http_status':200 if state!='UNKNOWN' else 429,'attempt_outcome':'ACCEPTED' if state!='UNKNOWN' else 'UNKNOWN'} for i,state in enumerate(['CONFIRMED','ACCEPTED','UNKNOWN','PARTIAL','REJECTED','PREPARED'])]
mutations=[]
def fixtures(role='platform_admin',error=None,empty=False,missing_problems=False):
    def handle(route):
        req=route.request;url=urllib.parse.urlparse(req.url);path=url.path
        if req.method!='GET':mutations.append(path)
        if error and error in path:return route.fulfill(status=503,json={'error':{'message':'合成接口读取失败'}})
        data=[]
        if path.endswith('/auth/me'):data={'display_name':'界面预览（合成）','code':'synthetic-admin','roles':[role],'password_session':False}
        elif path.endswith('/readiness'):data={'profiles':[{'profile':'alihealth_yljg_retail_v1','label':'零售上报','method':'synthetic.method','destination':scope['destination'],'capabilities':[{'capability':'send','status':'APPROVED','missing':[]}],'signature_verified':True}],'layers':{'NETWORK':{'status':'UNKNOWN'}}}
        elif path.endswith('/send-scope'):data=scope
        elif path.endswith('/upload-stats'):data={'days':[] if empty else days}
        elif path.endswith('/daily-reports'):data=[] if empty else [report,manual]
        elif '/daily-reports/' in path:data=[{**report,'window':['2026-10-25T00:00:00','2026-10-26T00:00:00'],'send':{'sent':567,'errors':[{'bill':'SYNTHETIC-BILL','step':'prepare','res':{'error_code':'DICTIONARY_UNMAPPED'}}]},'query_stats':{'skipped_exhausted':247}},{**manual,'window':['2026-10-25T00:00:00','2026-10-26T00:00:00'],'send':{'sent':815,'skipped_terminal':222},'query_stats':{}}]
        elif path.endswith('/submission-outcomes'):
            problem='only=problem' in url.query
            data=[] if empty or (missing_problems and problem) else rows
            if missing_problems and not problem:data=[rows[0]]
        elif path.endswith('/bills'):data=[{'id':'synthetic-bill','source_bill_no':'仅测试单据','pharmacy_code':'050102','business_kind':'PATIENT_DISPENSE','quality_state':'VALID','source_revision':'001','code_count':2,'finalized':True,'bill_time':'2026-10-25T08:00:00'}]
        elif path.endswith('/connectors'):data=[{'id':'synthetic-source','direction':'source','plugin_id':'synthetic','name':'合成测试视图'}]
        return route.fulfill(status=200,json={'data':data,'request_id':'SYNTHETIC-UI-ONLY'})
    return handle

result={'synthetic':True,'hospital_or_platform_connections':0,'checks':{}}
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        def context(role='platform_admin',**options):
            ctx=browser.new_context(viewport={'width':1440,'height':1100},timezone_id='America/Los_Angeles')
            ctx.add_init_script('sessionStorage.setItem("blyb.token","SYNTHETIC-PREVIEW");localStorage.setItem("blyb.roles",'+json.dumps(json.dumps([role]))+');')
            ctx.route('**/api/v1/**',fixtures(role=role,**options))
            return ctx
        ctx=context();page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(type(e).__name__))
        page.goto(base+'/ui/#/drugtrace',wait_until='networkidle')
        page.get_by_role('heading',name='上报总览',exact=True).wait_for()
        assert [x.strip() for x in page.locator('.metric-value').all_text_contents()]==['1,248笔','41笔','206笔','5笔']
        result['checks']['four_clear_metrics']=True
        assert page.get_by_role('heading',name='接入条件与授权明细',exact=True).count()==0
        result['checks']['settings_hidden_from_overview']=True
        page.add_style_tag(content='body::after{content:"界面预览 · 全部为合成数据";position:fixed;bottom:10px;right:20px;background:#ffffffee;border:1px solid #ccd8e1;padding:6px 12px;font:12px sans-serif;color:#60758a;border-radius:6px;z-index:1000}')
        page.screenshot(path=str(EVID/'overview-desktop.png'),full_page=True)
        page.get_by_role('button',name='查看记录').filter(has_text='').count()
        page.locator('.metric-card.unknown').click();page.wait_for_timeout(150)
        assert page.locator('.records-pane tbody tr').filter(has_text='DEMO-2').count()==1
        result['checks']['metric_opens_matching_records']=True
        page.get_by_role('button',name='查看详情',exact=True).first.click()
        page.get_by_role('dialog').wait_for()
        assert page.get_by_role('dialog').get_by_text('药房、企业或业务字典尚未完成映射',exact=True).is_visible()
        page.get_by_role('button',name='关闭',exact=True).click()
        page.get_by_role('button',name='查看详情',exact=True).nth(1).click()
        page.get_by_role('dialog').locator('.detail-summary').get_by_text('815',exact=True).wait_for()
        assert page.get_by_role('dialog').locator('.detail-summary').get_by_text('222',exact=True).is_visible()
        assert page.get_by_role('dialog').get_by_text('药房、企业或业务字典尚未完成映射',exact=True).count()==0
        result['checks']['same_day_manual_detail_matches_source']=True
        page.get_by_role('button',name='关闭',exact=True).click()
        page.get_by_role('button',name='总览',exact=True).click()
        page.set_viewport_size({'width':1000,'height':1000});page.screenshot(path=str(EVID/'overview-compact.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        result['checks']['compact_no_page_overflow']=True
        page.set_viewport_size({'width':720,'height':1000})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(EVID/'overview-narrow.png'),full_page=True)
        result['checks']['narrow_no_page_overflow']=True
        page.get_by_role('button',name='接入与授权',exact=True).click();page.wait_for_timeout(100)
        assert page.get_by_role('button',name='按当前缺项登记全部证据（仅演练）',exact=True).count()==0
        result['checks']['demo_approval_hidden_in_real_mode']=True
        page.get_by_text('停发操作',exact=True).click()
        page.once('dialog',lambda dialog:dialog.dismiss())
        page.get_by_role('button',name='撤销上报证据并停发',exact=True).click()
        result['checks']['stop_requires_explicit_confirmation']=True
        page.get_by_role('button',name='上报记录',exact=False).filter(has_text='上报记录').first.click()
        page.locator('.record-filters select').select_option('PREPARED')
        page.once('dialog',lambda dialog:dialog.dismiss())
        page.get_by_role('button',name='发送单据',exact=True).click()
        result['checks']['manual_send_cancel_has_no_post']=True
        assert not errors;ctx.close()
        ctx=context(error='upload-stats');page=ctx.new_page();page.goto(base+'/ui/#/drugtrace',wait_until='networkidle')
        assert all('—' in x for x in page.locator('.metric-value').all_text_contents())
        assert page.get_by_role('alert').filter(has_text='统计读取失败').is_visible()
        result['checks']['failed_statistics_not_zero_success']=True;ctx.close()
        ctx=context(empty=True);page=ctx.new_page();page.goto(base+'/ui/#/drugtrace',wait_until='networkidle')
        assert page.get_by_text('所选时间范围内暂无提交',exact=True).is_visible()
        assert page.get_by_text('所选范围内的提交均已确认成功',exact=True).count()==0
        result['checks']['empty_not_healthy_claim']=True;ctx.close()
        ctx=context(missing_problems=True);page=ctx.new_page();page.goto(base+'/ui/#/drugtrace',wait_until='networkidle')
        assert page.get_by_text('汇总仍有待处理记录，近期明细暂未完整显示。请以汇总为准，不要据空列表判定已处理完。',exact=True).is_visible()
        page.locator('.metric-card.unknown').click();page.wait_for_timeout(100)
        assert page.locator('.records-pane .notice.warning').is_visible()
        result['checks']['incomplete_problem_list_not_false_clear']=True;ctx.close()
        ctx=context(role='auditor');page=ctx.new_page();page.goto(base+'/ui/#/drugtrace',wait_until='networkidle')
        page.get_by_role('button',name='单据与追溯码',exact=True).click();page.wait_for_timeout(100)
        assert page.get_by_role('button',name='准备提交',exact=True).count()==0
        page.get_by_role('button',name='接入与授权',exact=True).click();page.wait_for_timeout(100)
        assert page.get_by_role('button',name='撤销上报证据并停发',exact=True).count()==0
        result['checks']['auditor_has_no_mutation_controls']=True;ctx.close()
        browser.close()
    assert not mutations,mutations
    result.update(mutation_requests=0,passed=True)
finally:
    server.shutdown();server.server_close()
    (EVID/'browser-review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps(result,ensure_ascii=False))
