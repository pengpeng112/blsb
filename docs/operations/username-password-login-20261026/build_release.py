"""Offline additive release based on the currently deployed dependency lock."""
from pathlib import Path
HERE=Path(__file__).resolve().parent
source=(HERE.parent/'drug-trace-ntp-dashboard-20261025/build_release.py').read_text('utf8')
source=source.replace('src-20261022T103000Z','queue-20261026T010000Z-r2')
source=source.replace("RID='ui-20261025T140000Z'","RID='auth-20261026T043500Z'")
exec(compile(source,'offline-password-release','exec'))
