"""Reuse the accepted offline builder and previous idle upgrade protocol."""
from pathlib import Path
import json
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
RID='flow-20261026T132000Z'
builder=(HERE.parent/'drug-trace-ntp-dashboard-20261025/build_release.py').read_text('utf8')
builder=builder.replace('src-20261022T103000Z','ui-clarity-20261026T063000Z').replace("RID='ui-20261025T140000Z'","RID="+repr(RID))
# Reject unrelated runtime/configuration changes before installing anything.
builder+='''
m=json.loads((OUT/'manifest.json').read_text('utf8'))
allowed={'apps/worker/engine.py','packages/connector_sdk/__init__.py','packages/workflow_core/jobs.py',
'plugins/destinations/alihealth_drugtrace/rate_control.py','plugins/destinations/alihealth_drugtrace/transport.py',
'plugins/destinations/alihealth_drugtrace/protocol.py','plugins/destinations/alihealth_drugtrace/client.py',
'plugins/domains/drug_trace/service.py','plugins/domains/drug_trace/plugin.py',
'deploy/compose/drug-trace.override.yml','deploy/dmz/forwarder.py',
'tests/integration/test_drug_trace_upload_flow.py','tests/integration/test_drug_trace_flow_pg.py',
'tests/integration/test_drug_trace_daily_driver_repair.py'}
assert set(m['delta']['files'])<=allowed,'UNRELATED_RELEASE_DELTA'
'''
(HERE/'build_release.py').write_text(builder,encoding='utf8')
transfer=(HERE.parent/'drug-trace-ui-clarity-20261026/transfer_release.py').read_text('utf8').replace('ui-clarity-20261026T063000Z',RID)
(HERE/'transfer_release.py').write_text(transfer,encoding='utf8')
upgrade=(HERE.parent/'drug-trace-ui-clarity-20261026/upgrade_remote.py').read_text('utf8')
upgrade=upgrade.replace("RID='ui-clarity-20261026T063000Z'","RID="+repr(RID)).replace("=='auth-20261026T043500Z'","=='ui-clarity-20261026T063000Z'")
upgrade=upgrade.replace("manifest['release_id']==RID and all(x.startswith('apps/admin_web/') for x in manifest['delta']['files'])", "manifest['release_id']==RID and manifest['delta']['base_release']=='ui-clarity-20261026T063000Z' and not manifest['delta']['dependencies_changed']")
(HERE/'upgrade_remote.py').write_text(upgrade,encoding='utf8')
for name in ['upgrade_status_remote.py','verify_remote.py','status_remote.py']:
    text=(HERE.parent/'drug-trace-ui-clarity-20261026'/name).read_text('utf8').replace('ui-clarity-20261026T063000Z',RID)
    (HERE/name).write_text(text,encoding='utf8')
# Keep the existing inspector's business metrics and active driver checks.
print(json.dumps({'release':RID,'base':'ui-clarity-20261026T063000Z','dependencies_unchanged':True}))
