"""Private worker preflight for the exact existing frozen bill; no upload."""
import json
import time
import http.client
from datetime import datetime, timedelta, timezone
from core_db import make_engine, make_session_factory
from core_db.models import Tenant, Submission, Destination, PackageRevision, DeliveryAttempt, ConnectorInstance, ConfigRevision
from domains.drug_trace import service as S, ingest, quality as Q
from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision, DrugTraceSendQuota

out = {"ok": False, "uploads": 0, "source_writes": 0}
engine = make_engine()
try:
    ref = json.load(open('/tmp/trial-clock.json'))
    elapsed = time.time() - ref['observed_local_epoch']
    assert -5 < elapsed < 180
    offset = time.time() - (ref['reference_epoch'] + elapsed)
    assert abs(offset) < 60, 'worker clock outside conservative single-trial guard'
    with make_session_factory(engine)() as db:
        tenant = db.query(Tenant).filter_by(code='first-hospital').one()
        sub = db.get(Submission,'7ed95aaa-ee66-4510-b80d-630f6c9dc381')
        assert sub.tenant_id == tenant.id and sub.state == 'PREPARED'
        assert db.query(DeliveryAttempt).filter_by(submission_id=sub.id).count() == 0
        dest = db.get(Destination,sub.destination_id)
        assert not dest.is_mock
        pr = db.get(PackageRevision,sub.package_revision_id)
        assert pr.state == 'FROZEN' and pr.provenance['config_release_id'] == dest.config_revision
        rev = db.get(DrugTraceBillRevision,pr.provenance['bill_revision_id'])
        bill = db.get(DrugTraceBill,rev.bill_id)
        assert rev.quality_state == 'VALID' and bill.current_revision_id == rev.id
        connector = db.get(ConnectorInstance,bill.connector_id)
        cfg = db.get(ConfigRevision,connector.active_config_revision)
        wh = S.make_warehouse(cfg.content_json)
        key = {k:getattr(bill,k) for k in ('source_system','source_bill_id','business_kind','pharmacy_code')}
        header = wh.fetch_all('drugtrace.bill_header',key,2)
        assert len(header) == 1
        lines, checked, _ = ingest.read_bill_whole(wh,header[0],code_page_limit=2)
        assert ingest.content_digest(checked,lines) == rev.content_digest, 'source changed since freeze'
        state, issues = Q.evaluate_revision(checked,lines,profile=pr.payload['profile'],code_limit_guard=3500)
        assert state == 'VALID'
        client = S.build_client(db,tenant.id,dest,purpose='query')
        tr = client.transport
        conn = http.client.HTTPSConnection(tr.host,tr.port,context=tr._ssl_ctx,timeout=10)
        try:
            conn.request('GET','/health')
            response=conn.getresponse(); body=response.read(8192)
            assert response.status == 200 and json.loads(body)['status'] == 'UP'
        finally:conn.close()
        biz = S.build_biz_params(pr.payload)
        assert len(biz['trace_codes'].split(',')) == 4
        bt = datetime.fromisoformat(pr.payload['bill_time'])
        details = {'submission_id':sub.id,'destination_id':dest.id,'config_release_id':dest.config_revision,
                   'scope':{'window_begin':bt.isoformat(),'window_end':(bt+timedelta(seconds=1)).isoformat(),
                            'pharmacies':[pr.payload['pharmacy_code']],'max_bills':1},
                   'checked_at':datetime.now(timezone.utc).isoformat(),'clock_offset_seconds':round(offset,3),
                   'strict_mtls':True,'source_snapshot_matches':True,'code_count':4,
                   'previous_vendor_upload_status':'UNKNOWN','upload_permission':'TO_BE_VERIFIED_BY_REQUEST'}
        import os
        fd=os.open('/tmp/trial-preflight.json',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as f:json.dump(details,f)
        out.update(ok=True,submission_state=sub.state,source_snapshot_matches=True,strict_mtls=True,
                   clock_offset_seconds=round(offset,3),code_count=4,max_bills=1,
                   previous_vendor_upload_status='UNKNOWN')
        db.rollback()
except Exception as exc:
    out['error_type']=type(exc).__name__
finally:engine.dispose()
print('__SOURCE_RESULT__'+json.dumps(out)+'__END__')
if not out['ok']:raise SystemExit(1)
