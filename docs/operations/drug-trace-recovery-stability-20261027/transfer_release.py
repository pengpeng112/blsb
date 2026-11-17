from pathlib import Path
import importlib.util,sys
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('ui_clarity_transfer',ROOT/'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb=importlib.util.module_from_spec(spec);spec.loader.exec_module(fb)
fb.deploy.load_creds=fb.creds
sys.argv=['deploy_remote.py','transfer',str(ROOT/'implementation/var/stability-20261027T030000Z-r2')]
raise SystemExit(fb.deploy.main())
