"""Offline runtime-preserving release; package whitelist and same-origin UI."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[3]
IMPL=ROOT/'implementation'
BASE=IMPL/'var/flow-20261026T132000Z'
RID='stability-20261027T030000Z-r2'
OUT=IMPL/'var'/RID


def run(args):
    p=subprocess.run(args,capture_output=True,text=True,encoding='utf8')
    if p.returncode:raise RuntimeError('command failed: '+' '.join(args[:4])+'\n'+p.stderr[-2000:])
    return p.stdout.strip()


def main():
    spec=importlib.util.spec_from_file_location('release_tools',IMPL/'deploy/release/build_release.py')
    rel=importlib.util.module_from_spec(spec);spec.loader.exec_module(rel)
    rel.gate_context()
    manifest=json.loads((BASE/'manifest.json').read_text('utf8'))
    for line in (BASE/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split('  ',1)
        if hashlib.sha256((BASE/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('base hash drift:'+name)
    for key,tag in manifest['image_tags'].items():
        probe=subprocess.run(['docker','inspect','--format','{{.Id}}',tag],capture_output=True,text=True)
        if probe.returncode:
            print(run(['docker','load','-i',str(BASE/'images'/('blyb-'+key+'.tar'))]),flush=True)
        assert run(['docker','inspect','--format','{{.Id}}',tag])==manifest['images'][key]
    if OUT.exists():raise RuntimeError('immutable release output exists')
    rel.build_frontend()
    (OUT/'images').mkdir(parents=True)
    changed=[]
    for src in rel.whitelist_files():
        name=src.relative_to(IMPL).as_posix()
        target=OUT/'implementation'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,target)
        old=BASE/'implementation'/name
        if not old.exists() or src.read_bytes()!=old.read_bytes():changed.append(name)
    ctx=OUT/'build-context';ctx.mkdir()
    runtime_dirs=['apps','packages','plugins','profiles','migrations']
    for name in runtime_dirs:
        source=OUT/'implementation'/name
        if source.exists():shutil.copytree(source,ctx/name)
        else:(ctx/name).mkdir()
    shutil.copy2(IMPL/'_bootstrap.py',ctx/'_bootstrap.py')
    shutil.copytree(IMPL/'apps/admin_web/dist',ctx/'apps/admin_web/dist')
    dockerfile='FROM '+manifest['image_tags']['backend']+'\nUSER root\n'
    dockerfile+='RUN rm -rf /app/apps/admin_web/dist\n'
    for name in runtime_dirs+['_bootstrap.py']:
        dockerfile+=f'COPY --chown=10001:10001 {name} /app/{name}\n'
    dockerfile+='USER blyb\n'
    (ctx/'Dockerfile').write_text(dockerfile,encoding='utf8')
    tag=f'blyb/backend:{RID}'
    print(run(['docker','build','--network=none','--pull=false','-t',tag,str(ctx)]),flush=True)
    tags={};ids={}
    for key in ['backend','nginx','pg']:
        tags[key]=f'blyb/{key}:{RID}'
        if key!='backend':run(['docker','tag',manifest['image_tags'][key],tags[key]])
        ids[key]=run(['docker','inspect','--format','{{.Id}}',tags[key]])
        with (OUT/'images'/('blyb-'+key+'.tar')).open('wb') as f:
            subprocess.run(['docker','save',tags[key]],stdout=f,check=True)
    freeze=sorted(run(['docker','run','--rm','--network=none',tag,'pip','freeze']).splitlines())
    assert freeze==manifest['backend']['pip_freeze'],'dependency drift'
    manifest.update(release_id=RID,built_at=datetime.now(timezone.utc).isoformat(),images=ids,image_tags=tags,
        backend={**manifest['backend'],'tag':tag,'digest':ids['backend'],'base_image':manifest['images']['backend']},
        delta={'base_release':'flow-20261026T132000Z','files':changed,'dependencies_changed':False})
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    shutil.copy2(BASE/'linux-runtime.lock.txt',OUT/'linux-runtime.lock.txt')
    sums=[f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(OUT).as_posix()}' for p in sorted(OUT.rglob('*')) if p.is_file()]
    (OUT/'SHA256SUMS').write_text('\n'.join(sums)+'\n',encoding='utf8')
    print(json.dumps({'release':RID,'changed':changed,'files':len(sums),'dependencies_unchanged':True}),flush=True)


if __name__=='__main__':main()

m=json.loads((OUT/'manifest.json').read_text('utf8'))
allowed={'plugins/sources/db_view/drug_trace.py', 'packages/workflow_core/jobs.py', 'plugins/domains/drug_trace/service.py', 'packages/workflow_core/lease_guard.py', 'plugins/domains/drug_trace/ingest.py', 'plugins/domains/drug_trace/plugin.py', 'apps/worker/engine.py'}
assert set(m['delta']['files'])==allowed,'UNRELATED_RELEASE_DELTA'
