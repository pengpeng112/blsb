"""Check this task's Markdown links and delivery records without global rewrites."""
from pathlib import Path
import importlib.util,json
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
spec=importlib.util.spec_from_file_location('local_document_links',ROOT/'scripts/project_docs.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
paths=[HERE/'README.md',ROOT/'docs/ai/sessions/DTR-UI-CLARITY-20261026.md']
errors=[]
for p in paths:
    for link in module.link_targets(p.read_text('utf8')):
        if '://' in link or link.startswith('#'):continue
        target=(p.parent/link.split('#',1)[0]).resolve()
        if not target.exists():errors.append({'file':p.name,'link':link})
assert not errors,errors
browser=json.loads((HERE/'evidence/browser-review.json').read_text('utf8'))
assert browser['passed'] and len(browser['checks'])==13 and browser['mutation_requests']==0
manifest=json.loads((ROOT/'implementation/var/ui-clarity-20261026T063000Z/manifest.json').read_text('utf8'))
assert len(manifest['delta']['files'])==4 and not manifest['delta']['dependencies_changed']
result={'new_document_links_valid':True,'synthetic_browser_checks':13,'real_test_mutations':0,'frontend_delta_files':4,'backend_delta':False}
(HERE/'evidence/final-local-checks.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps(result))
