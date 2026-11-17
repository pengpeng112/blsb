"""Reuse pinned, read-only DMZ probes, writing only this review's evidence."""
from pathlib import Path
import importlib.util
import contextlib
import io

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location('prior_dmz_probe', ROOT / 'docs/deployment/reporting-20261015/revision-7/probe_dmz.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
# The original module uses __file__ only to choose the output location inside main.
# Keep its BASE/ROOT/PRIVATE bindings and all key verification and read-only commands.
probe.__file__ = str(Path(__file__).resolve())
original_curl = probe.curl
probe.curl = lambda url: original_curl(url) + ' --ipv4'
with contextlib.redirect_stdout(io.StringIO()) as captured:
    code = probe.main()
for line in captured.getvalue().splitlines():
    if line.startswith(('Completed:', 'Evidence:')):
        print(line)
raise SystemExit(code)
