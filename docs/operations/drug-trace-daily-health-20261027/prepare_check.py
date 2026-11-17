"""Reuse reviewed read-only probes with sanitized error-code aggregation."""
from pathlib import Path
import shutil
HERE=Path(__file__).resolve().parent
shutil.copy2(HERE.parent/'drug-trace-history-retry-20261026/check.py',HERE/'check.py')
source=(HERE.parent/'drug-trace-daily-health-20261026/status_remote.py').read_text(encoding='utf8')
source=source.replace("left(last_error->>'message',160) error", "last_error->>'code' error_code")
compile(source,'daily-status','exec')
(HERE/'status_remote.py').write_text(source,encoding='utf8')
print('Prepared read-only daily probe')
