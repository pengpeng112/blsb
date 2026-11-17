from pathlib import Path
import importlib.util
import sys

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('queue_upgrade_channel',ROOT/'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb=importlib.util.module_from_spec(spec);spec.loader.exec_module(fb)
fb.deploy.load_creds=fb.creds
sys.argv=['deploy_remote.py','transfer',str(ROOT/'implementation/var/queue-20261026T010000Z-r2')]
raise SystemExit(fb.deploy.main())
