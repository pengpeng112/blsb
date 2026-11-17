"""Create a distinct manual-review revision; preserve all prior evidence."""
from pathlib import Path
import json
HERE=Path(__file__).resolve().parent
sample=json.loads(sorted((HERE/'evidence').glob('app-sample-*.json'))[-1].read_text('utf8'))['result']['rows']
prior=json.loads(sorted((HERE/'evidence').glob('app-progress-*.json'))[-1].read_text('utf8'))['result']['rows']
done={r['submission_id'] for r in sample+prior if r['platform_state'] in ('CONFIRMED','NOT_FOUND') and r['local_state']!='CONFIRMED'}
# The single rejected request was rechecked separately after cooldown and 3/51.
diagnosis=json.loads(sorted((HERE/'evidence').glob('app-diagnose-*.json'))[-1].read_text('utf8'))['result']['summary']
assert diagnosis['classification']=={'state':'CONFIRMED','result_type':'3','sub_process_flag':'51'}
done.add('5aaa9241-82f6-4bdb-b47a-608e55fd43ef')
assert len(done)==8
code=(HERE/'probe_container.py').read_text('utf8')
code=code.replace("else:targets=[control]+targets", "else:targets=[control]+[r for r in targets if r.id not in "+repr(done)+"]")
code=code.replace("guarded=QueryOnlyTransport(client.transport);client.transport=guarded", "client.transport._pacer.interval=3.2\n            guarded=QueryOnlyTransport(client.transport);client.transport=guarded")
start=code.index('            full=S._query_full_windows(')
end=code.index('\n            cls=S._classify_query',start)
code=code[:start]+'''            windows=S._query_windows(payload,sub.created_at,semantics)
            # The original business/upload windows overlap for these targets.
            # Their union is identical coverage, with a single exact bill filter.
            assert len(windows)==1 or max(w[0] for w in windows)<=min(w[1] for w in windows),'DISJOINT_WINDOWS'
            begin=min(w[0] for w in windows);end=max(w[1] for w in windows)
            full=S._platform_query_full(client,{'ref_ent_id':str(payload['ref_ent_id']),'bill_code':str(payload['bill_code']),'bill_type':str(payload['query_bill_type']),'begin_date':begin,'end_date':end})
            full['windows_queried']=1
''' .rstrip()+code[end:]
compile(code,'manual-review-r2','exec')
launcher=(HERE/'all_remote.py').read_text('utf8')
start=launcher.index('(folder/\'probe.py\').write_text(');end=launcher.index('\nwrapper=',start)
launcher=launcher[:start]+"(folder/'probe.py').write_text("+repr(code)+')'+launcher[end:]
launcher=launcher.replace('unknown-review-20261026','unknown-review-20261026-r2')
compile(launcher,'manual-review-r2-launcher','exec')
(HERE/'resume_remote.py').write_text(launcher,encoding='utf8')
progress=(HERE/'progress_remote.py').read_text('utf8').replace('unknown-review-20261026','unknown-review-20261026-r2')
(HERE/'progress2_remote.py').write_text(progress,encoding='utf8')
(HERE/'evidence/resume-plan.json').write_text(json.dumps({'previously_conclusively_checked':len(done),'remaining':239,'positive_controls':1,'minimum_request_interval_seconds':3.2,'query_windows':'union of overlapping business/upload windows; same exact bill identity','uploads':0,'business_writes':0},indent=2)+'\n',encoding='utf8')
print('Revision2:239 remaining exact-bill lookups plus1 known-success control; prior evidence preserved')
