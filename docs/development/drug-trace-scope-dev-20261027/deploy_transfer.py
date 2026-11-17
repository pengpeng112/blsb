"""Phase 1：发行包传输（复用 deploy_remote 既有 transfer 机制，同 stability 轮）。"""
from pathlib import Path
import importlib.util, sys
ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    'scope_transfer', ROOT / 'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)
fb.deploy.load_creds = fb.creds
sys.argv = ['deploy_remote.py', 'transfer',
            str(ROOT / 'implementation/var/scope-20261028')]
raise SystemExit(fb.deploy.main())
