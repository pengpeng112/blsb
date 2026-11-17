"""Read only the three requested reports' production receipts; emit aggregates.

No deployment, config writes, source DB connections or HTTP requests are made.
Credentials are handled by the existing pinned-host deployment helper in memory.
"""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import re
import shlex
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent


def main():
    cases = list((ROOT / '数据上报/4、传染病病历数据上报').glob('病例核查说明-*-20261115.md'))
    if len(cases) != 1:
        raise RuntimeError('Case document is ambiguous; select its scope explicitly')
    case = cases[0]
    text = case.read_text(encoding='utf-8')
    keys = list(dict.fromkeys(re.findall(r'\b261114\d{4}\b', text)))
    if len(keys) != 3:
        raise RuntimeError('Expected exactly the three case report keys')
    helper = ROOT / 'docs/deployment/reporting-20261015/deploy_remote.py'
    spec = importlib.util.spec_from_file_location('existing_deploy_channel', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    remote = r'''
from pathlib import Path
import ast, hashlib, json, time
keys = __KEYS__
labels = {key: 'P'+str(i+1) for i,key in enumerate(keys)}
base = Path('/data/exchange/inf-pusher')
result = {'mode': 'READ_ONLY_NO_SEND', 'timezone': list(time.tzname), 'files': [], 'runtime': {}}
for name in ('pusher.py','tables.py','run.sh'):
    data=(base/name).read_bytes()
    info={'sha256':hashlib.sha256(data).hexdigest()}
    if name=='tables.py':
        for n in ast.parse(data.decode('utf-8')).body:
            if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ROLL_OUT_ORDER' for t in n.targets):
                info['roll_out_order']=ast.literal_eval(n.value)
    result['runtime'][name]=info
for filename in ():
    path=base/'state'/filename
    info={'file':filename,'exists':path.exists(),'groups':{},'complete':True}
    if not path.exists():
        result['files'].append(info);continue
    before=path.stat().st_size
    deadline=time.monotonic()+45
    scanned=0
    with path.open(encoding='utf-8') as stream:
        for index,line in enumerate(stream,1):
            scanned=index
            if index%10000==0 and time.monotonic()>deadline:
                info['complete']=False;break
            if not any(k in line for k in keys):continue
            try:r=json.loads(line)
            except ValueError:continue
            table=r.get('table')
            if table not in ('ex_lab','ex_lab_item'):continue
            rid=str(r.get('id','')).split('/',1)[-1]
            key=next((k for k in keys if (rid==k if table=='ex_lab' else rid.startswith(k))),None)
            if key is None:continue
            group=info['groups'].setdefault(labels[key]+'/'+table,{'records':0,'accepted':0,'mock':0,'failed':0,'first_accepted_ts':None,'last_accepted_ts':None,'first_accepted_line':None,'last_accepted_line':None,'unique_accepted_ids':set()})
            group['records']+=1
            mock=r.get('mode')=='mock' or r.get('stage')=='mock'
            accepted=(filename=='sent.jsonl') or (r.get('result') is True and not mock)
            if mock:group['mock']+=1
            elif accepted:
                group['accepted']+=1
                group['unique_accepted_ids'].add(rid)
                if group['first_accepted_line'] is None:
                    group['first_accepted_line']=index;group['first_accepted_ts']=r.get('ts')
                group['last_accepted_line']=index;group['last_accepted_ts']=r.get('ts')
            else:group['failed']+=1
    info.update(lines_scanned=scanned,bytes_before=before,bytes_after=path.stat().st_size)
    for group in info['groups'].values():
        group['unique_accepted_ids']=len(group['unique_accepted_ids'])
    result['files'].append(info)
print('EVIDENCE_JSON_BEGIN')
print(json.dumps(result,ensure_ascii=False))
print('EVIDENCE_JSON_END')
'''.replace('__KEYS__', repr(keys))
    command = 'docker exec <worker-container> python -c ' + shlex.quote(remote)
    creds = module.load_creds()
    jump = module.connect_jump(creds['jump'])
    started = time.monotonic()
    try:
        rc, transcript = module.nested_exec(jump, creds, command, timeout=180)
    finally:
        jump.close()
    # Do not emit the SSH transcript or credential-related exceptions.
    matched = re.search(r'EVIDENCE_JSON_BEGIN\s*(\{[^\r\n]+\})\s*EVIDENCE_JSON_END', transcript)
    if rc != 0 or not matched:
        print(json.dumps({'ok':False,'remote_exit_code':rc,'evidence_marker_found':bool(matched)}))
        return 2
    evidence = json.loads(matched.group(1))
    evidence['elapsed_seconds'] = round(time.monotonic()-started,2)
    evidence['remote_exit_code'] = rc
    evidence['local_hashes'] = {name:hashlib.sha256((ROOT/'implementation/deploy/inf_pusher'/name).read_bytes()).hexdigest() for name in ('pusher.py','tables.py','run.sh')}
    encoded = json.dumps(evidence,ensure_ascii=False,indent=2)
    for profile in creds.values():
        secret = profile.get('password')
        if secret and secret in encoded:
            raise RuntimeError('Output rejected by secret check')
    (HERE/'runtime-code.json').write_text(encoded+'\n',encoding='utf-8')
    print(encoded)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
