"""Verify review docs and current uncommitted baseline; no credentials/network."""
from pathlib import Path
from datetime import datetime, timezone
import ast, hashlib, json, re, subprocess
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[5];BASE=Path(__file__).resolve().parents[1];OUT=BASE/'evidence'
target=OUT/'verification.json'
if not target.exists():target.write_text('{}\n',encoding='utf-8')
baseline=json.loads((OUT/'baseline-hashes.json').read_text(encoding='utf-8'))
allowed={'docs/ai/STATE.md','docs/ai/HANDOFF.md','docs/ai/BACKLOG.md'}
changed=[f for f,h in baseline.items() if f not in allowed and
         (not (ROOT/f).is_file() or hashlib.sha256((ROOT/f).read_bytes()).hexdigest()!=h)]
docs=list(BASE.glob('*.md'))+[ROOT/x for x in allowed]+[ROOT/'docs/README.md',ROOT/'docs/ai/sessions/DTR-REVIEW3-20261017.md']
links=0;bad=[]
for p in docs:
 for x in re.findall(r'\[[^\]]*\]\(([^)]+)\)',p.read_text(encoding='utf-8')):
  x=x.strip('<>').split('#',1)[0]
  if not x or re.match(r'^[a-z]+://',x):continue
  links+=1
  if not (p.parent/x).resolve().exists():bad.append({'file':p.relative_to(ROOT).as_posix(),'target':x})
syntax=[]
for p in OUT.glob('*.py'):ast.parse(p.read_text(encoding='utf-8'));syntax.append(p.name)
def xml(path):
 ss=list(ET.parse(path).getroot().iter('testsuite'))
 d={k:sum(int(s.get(k,0)) for s in ss) for k in ('tests','errors','failures','skipped')}
 d['passed']=d['tests']-d['errors']-d['failures']-d['skipped'];return d
tests=xml(OUT/'local-tests.xml');hist=xml(ROOT/'implementation/var/fix2-full-regression.xml')
probes=json.loads((OUT/'probe-results.json').read_text(encoding='utf-8'))
diff=subprocess.run(['git','diff','--check'],cwd=ROOT,capture_output=True,text=True)
own_diff=subprocess.run(['git','diff','--check','--','docs/ai','docs/README.md',str(BASE.relative_to(ROOT))],
                       cwd=ROOT,capture_output=True,text=True)
(OUT/'global-diff-check.txt').write_text(diff.stdout+'\n'+diff.stderr,encoding='utf-8')
report={'at_utc':datetime.now(timezone.utc).isoformat(),'protected_baseline_files':len(baseline)-len(allowed),
 'unexpected_changes':changed,'local_links_checked':links,'broken_links':bad,'script_syntax':syntax,
 'local_tests':tests,'historical_full_regression_not_rerun':hist,
 'counterexamples':len(probes['observations']),'reproduced':sum(x['issue_reproduced'] for x in probes['observations']),
 'confirmed_fixes':probes['confirmed_fixes'],'git_diff_check_exit_code':diff.returncode,
 'review_scope_diff_check_exit_code':own_diff.returncode,
 'global_diff_note':'Pre-existing modified implementation/docs/测试执行记录.md has new blank line at EOF; this review does not edit it.',
 'credentials_read':False,'remote_access':False}
report['passed']=(not changed and not bad and own_diff.returncode==0 and tests['passed']==33 and
 tests['failures']==0 and len(probes['observations'])==14 and all(x['issue_reproduced'] for x in probes['observations']))
target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(0 if report['passed'] else 1)
