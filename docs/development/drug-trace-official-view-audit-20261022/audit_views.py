"""Bounded, user-authorized live view audit. No business keys/codes leave <relay-jump>."""
from pathlib import Path
import importlib.util
import contextlib
import io
import json
import sys
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('base_reader',ROOT/'docs/development/drug-trace-view-verify-20261022/verify_reader.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
base.HERE=Path(__file__).resolve().parent
prefix=base.REMOTE.split('try:\n    auth=query')[0]
base.REMOTE=prefix+Path(__file__).with_name('audit_queries.remote.py').read_text(encoding='utf8')
if __name__=='__main__':
    # The shared helper stores full metadata evidence; keep terminal output short.
    with contextlib.redirect_stdout(io.StringIO()):
        result=base.main()
    phase=sys.argv[1] if len(sys.argv)>1 else 'r1'
    evidence=base.HERE/'evidence'/('reader-'+phase+'.json')
    data=json.loads(evidence.read_text(encoding='utf8'))
    queries=data.get('queries',{})
    failed=[key for key,value in queries.items() if not value.get('ok')]
    print(json.dumps({'queries':len(queries),'failed_queries':failed,
                      'source_writes':data.get('source_writes'),
                      'uploads':data.get('uploads'),'evidence':str(evidence)},ensure_ascii=False))
    raise SystemExit(result or int(bool(failed)))
