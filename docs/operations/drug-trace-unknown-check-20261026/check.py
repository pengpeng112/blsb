"""Existing pinned SSH channel; sanitized evidence only."""
from pathlib import Path
import importlib.util
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('unknown_check_remote',HERE.parent/'drug-trace-ntp-dashboard-20261025/remote.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.HERE=HERE
raise SystemExit(module.main())
