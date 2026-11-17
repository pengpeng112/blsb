"""Current app/DMZ readiness using existing pinned SSH helpers; readonly."""
from pathlib import Path
import importlib.util
import sys

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent

def main():
    path=ROOT/'docs/development/drug-trace-view-verify-20261022/recheck_runtime.py'
    spec=importlib.util.spec_from_file_location('r10_runtime_probe',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.HERE=HERE
    return module.main()

if __name__=='__main__':raise SystemExit(main())
