"""Resume the62 remaining rows after a later user request and quota recovery."""
from pathlib import Path
import ast,json
HERE=Path(__file__).resolve().parent
proof=json.loads(sorted((HERE/'evidence').glob('app-recheck_limit-*.json'))[-1].read_text('utf8'))['result']['summary']
assert proof['classification']['state']=='NOT_FOUND' and proof['http']==[200,200] and not proof['top_errors']
done={'5aaa9241-82f6-4bdb-b47a-608e55fd43ef','60291343-4a03-4a35-a1ad-2ece7a38b3be'}
for phase in ['sample','progress','progress2','progress3']:
 data=json.loads(sorted((HERE/'evidence').glob('app-'+phase+'-*.json'))[-1].read_text('utf8'))['result']
 for r in data['rows']:
  if r['local_state']!='CONFIRMED' and r['platform_state'] in ('CONFIRMED','NOT_FOUND'):done.add(r['submission_id'])
assert len(done)==185
launcher=(HERE/'finalpass_remote.py').read_text('utf8');tree=ast.parse(launcher)
node=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='write_text' and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str) and 'make_session_factory' in n.args[0].value)
code=node.args[0].value
start=code.index('        else:targets=[control]+[r for r in targets if r.id not in ');end=code.index('\n',start)
code=code[:start]+'        else:targets=[control]+[r for r in targets if r.id not in '+repr(done)+']'+code[end:]
compile(code,'evening-resume-probe','exec')
start=launcher.index("(folder/'probe.py').write_text(");end=launcher.index('\nwrapper=',start)
launcher=launcher[:start]+"(folder/'probe.py').write_text("+repr(code)+')'+launcher[end:]
launcher=launcher.replace('unknown-review-20261026-r3','unknown-review-20261026-r4')
compile(launcher,'evening-resume-launcher','exec')
(HERE/'evening_remote.py').write_text(launcher,encoding='utf8')
(HERE/'progress4_remote.py').write_text((HERE/'progress3_remote.py').read_text('utf8').replace('unknown-review-20261026-r3','unknown-review-20261026-r4'),encoding='utf8')
print(json.dumps({'prior_conclusive':185,'remaining':62,'known_success_controls':1,'interval_seconds':4.5,'uploads':0}))
