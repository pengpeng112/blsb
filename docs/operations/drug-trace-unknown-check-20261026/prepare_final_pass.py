"""Final bounded pass for63 unverified targets; no repeated successful lookups."""
from pathlib import Path
import ast,json
HERE=Path(__file__).resolve().parent
done=set()
for phase in ['sample','progress','progress2']:
    data=json.loads(sorted((HERE/'evidence').glob('app-'+phase+'-*.json'))[-1].read_text('utf8'))['result']
    for r in data['rows']:
        if r['local_state']!='CONFIRMED' and r['platform_state'] in ('CONFIRMED','NOT_FOUND'):done.add(r['submission_id'])
done.add('5aaa9241-82f6-4bdb-b47a-608e55fd43ef')
assert len(done)==184
launcher=(HERE/'resume_remote.py').read_text('utf8')
tree=ast.parse(launcher)
node=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='write_text' and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str) and 'make_session_factory' in n.args[0].value)
code=node.args[0].value
start=code.index('        else:targets=[control]+[r for r in targets if r.id not in ')
end=code.index('\n',start)
code=code[:start]+'        else:targets=[control]+[r for r in targets if r.id not in '+repr(done)+']'+code[end:]
code=code.replace('client.transport._pacer.interval=3.2','client.transport._pacer.interval=4.5')
code=code.replace("self.original=transport;self.calls=0;self.http=Counter()", "self.original=transport;self.calls=0;self.http=Counter();self.top_error=None")
code=code.replace('        self.http[str(result.http_status)]+=1', '''        self.http[str(result.http_status)]+=1
        try:
            error=json.loads(result.body).get('error_response')
            if error:self.top_error={k:error.get(k) for k in ('code','msg','sub_code')}
        except (ValueError,TypeError):pass''')
code=code.replace("'platform_calls':guarded.calls,'http':dict(guarded.http)","'platform_calls':guarded.calls,'top_error':guarded.top_error,'http':dict(guarded.http)")
compile(code,'final-pass-probe','exec')
start=launcher.index("(folder/'probe.py').write_text(");end=launcher.index('\nwrapper=',start)
launcher=launcher[:start]+"(folder/'probe.py').write_text("+repr(code)+')'+launcher[end:]
launcher=launcher.replace('unknown-review-20261026-r2','unknown-review-20261026-r3')
compile(launcher,'final-pass-launcher','exec')
(HERE/'finalpass_remote.py').write_text(launcher,encoding='utf8')
(HERE/'progress3_remote.py').write_text((HERE/'progress2_remote.py').read_text('utf8').replace('unknown-review-20261026-r2','unknown-review-20261026-r3'),encoding='utf8')
print(json.dumps({'already_conclusive':len(done),'remaining':63,'minimum_interval_seconds':4.5,'uploads':0}))
