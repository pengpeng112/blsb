"""Offline UI release; rejects any backend runtime or deployment configuration delta."""
from pathlib import Path
import json
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
source=(HERE.parent/'drug-trace-ntp-dashboard-20261025/build_release.py').read_text('utf8')
source=source.replace('src-20261022T103000Z','auth-20261026T043500Z')
source=source.replace("RID='ui-20261025T140000Z'","RID='ui-clarity-20261026T063000Z'")
exec(compile(source,'offline-ui-clarity-release','exec'))
manifest=json.loads((ROOT/'implementation/var/ui-clarity-20261026T063000Z/manifest.json').read_text('utf8'))
assert all(p.startswith('apps/admin_web/') for p in manifest['delta']['files']), 'NON_FRONTEND_DELTA'
