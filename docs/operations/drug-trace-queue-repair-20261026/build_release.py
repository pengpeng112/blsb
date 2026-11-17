"""Reuse reviewed offline release builder with the current verified base."""
from pathlib import Path

HERE=Path(__file__).resolve().parent
source=(HERE.parent/'drug-trace-ntp-dashboard-20261025/build_release.py').read_text('utf8')
source=source.replace('src-20261022T103000Z','ui-20261025T140000Z')
source=source.replace("RID='ui-20261025T140000Z'","RID='queue-20261026T010000Z-r2'")
exec(compile(source,'offline-queue-release','exec'))
