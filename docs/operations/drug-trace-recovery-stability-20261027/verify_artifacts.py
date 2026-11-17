"""Local release/evidence/link verification, no network or production changes."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import unquote
import ast,hashlib,json,re,xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
RID='stability-20261027T030000Z-r2';bundle=ROOT/'implementation/var'/RID
out={'utc':datetime.now(timezone.utc).isoformat(),'network_calls':0,'base_stability_release':RID}

manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf8'))
allowed={'apps/worker/engine.py','packages/workflow_core/jobs.py','packages/workflow_core/lease_guard.py',
    'plugins/domains/drug_trace/ingest.py','plugins/domains/drug_trace/plugin.py','plugins/domains/drug_trace/service.py',
    'plugins/sources/db_view/drug_trace.py'}
assert set(manifest['delta']['files'])==allowed and not manifest['delta']['dependencies_changed']
assert all((ROOT/'implementation'/n).read_bytes()==(bundle/'implementation'/n).read_bytes() for n in allowed)
out['seven_runtime_files_match_working_tree']=True
verified=0
for line in (bundle/'SHA256SUMS').read_text(encoding='utf8').splitlines():
    digest,name=line.split('  ',1);p=(bundle/name).resolve()
    assert p.is_relative_to(bundle.resolve())
    assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
    verified+=1
out['bundle_hashes_verified']=verified
baseline=json.loads((HERE/'evidence/baseline-files.json').read_text(encoding='utf8'))
for name,digest in baseline.items():assert hashlib.sha256((HERE/'baselines'/name).read_bytes()).hexdigest()==digest,name
out['baseline_files_unchanged']=len(baseline)
binding=json.loads((HERE/'evidence/deployment-bindings.json').read_text(encoding='utf8'))
driver=ROOT/'docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py'
phase_binding=HERE/'evidence/phase-deployment-bindings.json'
if phase_binding.exists():
    second=json.loads(phase_binding.read_text(encoding='utf8'))
    assert hashlib.sha256(driver.read_text(encoding='utf8').encode()).hexdigest()==second['driver_after']
    assert hashlib.sha256((HERE/'baselines/phase-driver/daily_upload_driver.py').read_text(encoding='utf8').encode()).hexdigest()==binding['new_driver']==second['driver_before']
else:assert hashlib.sha256(driver.read_text(encoding='utf8').encode()).hexdigest()==binding['new_driver']
assert hashlib.sha256((HERE/'recover_runner_resumed.py').read_text(encoding='utf8').encode()).hexdigest()==binding['runner_resumed_sha256_lf']
out['driver_and_resumed_binding_verified']=True

out['tests']={}
for name,path,expected in [
    ('application_full',ROOT/'implementation/var/stability-regression-r2.xml',(569,7)),
    ('local_including_pg',ROOT/'implementation/var/stability-local-final.xml',(81,0)),
    ('final_guard',ROOT/'implementation/var/stability-lease-final.xml',(15,0)),
    ('top7_operations',ROOT/'implementation/var/recovery-top7.xml',(26,0)),
    ('query_only_completion',HERE/'evidence/operations-tests.xml',(14,0)),
    ('final_driver_budget_local',ROOT/'implementation/var/stability-driver-budget.xml',(55,0)),
    ('final_application_driver_full',ROOT/'implementation/var/stability-regression-r3.xml',(602,7)),
]:
    suites=list(ET.parse(path).getroot().iter('testsuite'))
    values={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert (values['tests'],values['skipped'])==expected and values['failures']==values['errors']==0,(name,values)
    values['passed']=values['tests']-values['skipped'];out['tests'][name]=values
    if name=='application_full':out['skipped_reasons']=[x.attrib.get('message','') for x in ET.parse(path).getroot().iter('skipped')]

python_files=list(HERE.glob('*.py'))
for p in python_files:compile(p.read_text(encoding='utf8'),str(p),'exec')
out['operations_scripts_compiled']=len(python_files)

documents=[HERE/'README.md',HERE/'SOURCE-REVIEW.md',ROOT/'docs/ai/sessions/DTR-RECOVERY-STABILITY-20261027.md']
shared=[ROOT/'docs/ai'/n for n in ('STATE.md','HANDOFF.md','BACKLOG.md')]
shared+=[ROOT/'数据上报/1、码上放心平台/README.md']
shared+=[ROOT/'implementation/docs'/n for n in ('实施状态矩阵.md','测试执行记录.md','接口与设计差异.md','04-API目录对照矩阵.md','系统架构与流程说明.md')]
checked=0
for p in documents+shared:
    body=p.read_text(encoding='utf-8-sig')
    if p in shared:
        begin='<!-- DTR-RECOVERY-STABILITY-20261027:BEGIN -->';end='<!-- DTR-RECOVERY-STABILITY-20261027:END -->'
        assert body.count(begin)==body.count(end)==1
        body=body.split(begin,1)[1].split(end,1)[0]
    for raw in re.findall(r'\]\(([^\n)]+)\)',body):
        target=unquote(raw.strip().strip('<>')).split('#',1)[0]
        if not target or re.match(r'^(?:https?|mailto):',target):continue
        target=re.sub(r':\d+$','',target)
        path=Path(target) if re.match(r'^[A-Za-z]:',target) else p.parent/target
        assert path.exists(),(str(p),target)
        checked+=1
out['task_links_verified']=checked
catalog=json.loads((ROOT/'docs/catalog.json').read_text(encoding='utf8'))
for p in documents:
    name=p.relative_to(ROOT).as_posix();assert sum(row['path']==name for row in catalog['documents'])==1,name
out['catalog_entries_unique']=len(documents)

out['source_sql']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (HERE/'sql').glob('*.sql')}
assert len(out['source_sql'])==3
out['source_sql_live_executed']=False
phase=ROOT/'implementation/var/stability-20261027T064000Z-r3'
if phase.exists():
    new=json.loads((phase/'manifest.json').read_text(encoding='utf8'))
    frontend={'apps/admin_web/src/views/DrugTrace.vue','apps/admin_web/src/views/drugTraceDisplay.ts','apps/admin_web/tests/drugTraceDisplay.test.mjs'}
    assert new['delta']['base_release']==RID and set(new['delta']['files'])==frontend
    assert new['backend']['pip_freeze']==manifest['backend']['pip_freeze']
    for name in allowed:assert (phase/'implementation'/name).read_bytes()==(bundle/'implementation'/name).read_bytes()
    for name in frontend:assert (phase/'implementation'/name).read_bytes()==(ROOT/'implementation'/name).read_bytes()
    total=0
    for line in (phase/'SHA256SUMS').read_text(encoding='utf8').splitlines():
        digest,name=line.split('  ',1);p=(phase/name).resolve()
        assert p.is_relative_to(phase.resolve()) and hashlib.sha256(p.read_bytes()).hexdigest()==digest
        total+=1
    browser=json.loads((HERE/'evidence/browser-review.json').read_text(encoding='utf8'))
    assert browser['passed'] and browser['mutation_requests']==browser['hospital_or_platform_connections']==0
    assert len(browser['checks'])==18 and all(browser['checks'].values())
    out['ui_only_successor']={'release':new['release_id'],'only_three_frontend_files':True,'core_runtime_unchanged':True,
       'dependency_freeze_unchanged':True,'bundle_hashes_verified':total,'synthetic_browser_checks':18,'business_posts':0}
target=HERE/'evidence'/('artifact-verification-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in out.items() if k not in ('skipped_reasons','source_sql')},ensure_ascii=False))
