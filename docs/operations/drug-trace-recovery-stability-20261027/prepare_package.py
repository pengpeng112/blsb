"""Build a minimal offline delta from the current immutable production release."""
from pathlib import Path
HERE=Path(__file__).resolve().parent
RID='stability-20261027T030000Z-r2'
source=(HERE.parent/'drug-trace-upload-flow-20261026/build_release.py').read_text(encoding='utf8')
source=source.replace("BASE=IMPL/'var/ui-clarity-20261026T063000Z'","BASE=IMPL/'var/flow-20261026T132000Z'")
source=source.replace("RID='flow-20261026T132000Z'",'RID='+repr(RID))
source=source.replace("'base_release':'ui-clarity-20261026T063000Z'","'base_release':'flow-20261026T132000Z'")
allowed={'apps/worker/engine.py','packages/workflow_core/jobs.py','packages/workflow_core/lease_guard.py',
 'plugins/domains/drug_trace/service.py','plugins/domains/drug_trace/plugin.py','plugins/domains/drug_trace/ingest.py',
 'plugins/sources/db_view/drug_trace.py'}
source=source[:source.index('\nallowed=')]+ '\nallowed='+repr(allowed)+"\nassert set(m['delta']['files'])==allowed,'UNRELATED_RELEASE_DELTA'\n"
compile(source,'stability_builder','exec');(HERE/'build_release.py').write_text(source,encoding='utf8')
t=(HERE.parent/'drug-trace-upload-flow-20261026/transfer_release.py').read_text(encoding='utf8').replace('flow-20261026T132000Z',RID)
(HERE/'transfer_release.py').write_text(t,encoding='utf8')
print('Prepared minimal offline builder/transfer; no build or deployment yet')
