"""Use the existing APIs for the user's exact one-bill exception and send."""
import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

token=sys.stdin.readline().strip()
args=json.loads(sys.stdin.readline())
unused_candidate=json.loads(sys.stdin.read())
phase=args['phase']
out={'ok':False,'phase':phase,'calls':[],'approval_kind':'USER_SINGLE_BILL_EXCEPTION'}
sid='7ed95aaa-ee66-4510-b80d-630f6c9dc381'


def call(method,path,body=None):
    req=urllib.request.Request('http://localhost:8000/api/v1'+path,method=method,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req,timeout=40) as response:
            status,data=response.status,json.load(response)
    except urllib.error.HTTPError as exc:
        status,data=exc.code,json.loads(exc.read())
    out['calls'].append({'method':method,'path':path,'http_status':status})
    if status not in (200,201):
        error=data.get('error') or {}
        out['api_error']={'code':error.get('code'),'message':re.sub(r'\d{15,}','[CODE]',str(error.get('message','')))[:400]}
        raise RuntimeError('API rejected operation; never retry the send blindly')
    return data['data']


try:
    from core_db import make_engine, make_session_factory
    from core_db.models import Tenant,Submission,DeliveryAttempt,Destination,PackageRevision,Job
    from domains.drug_trace import contracts as C
    from domains.drug_trace.models import DrugTraceSendQuota
    engine=make_engine()
    with make_session_factory(engine)() as db:
        tenant=db.query(Tenant).filter_by(code='first-hospital').one()
        sub=db.get(Submission,sid)
        assert sub.tenant_id == tenant.id
        did=sub.destination_id
        cfg=db.get(Destination,did).config_revision
        attempts=db.query(DeliveryAttempt).filter_by(submission_id=sid).count()
        if phase in ('approve','send'):
            assert sub.state == 'PREPARED' and attempts == 0
            assert db.query(Job).filter_by(tenant_id=tenant.id,job_type=C.DomainJobType.SEND).count() == 0
        if phase == 'approve':
            assert db.query(DrugTraceSendQuota).filter_by(tenant_id=tenant.id).count() == 0
    engine.dispose()
    if phase == 'approve':
        facts=json.load(open('/tmp/trial-preflight.json'))
        checked=datetime.fromisoformat(facts['checked_at'])
        assert datetime.now(timezone.utc)-checked < timedelta(minutes=15)
        assert facts['submission_id'] == sid and facts['destination_id'] == did and facts['config_release_id'] == cfg
        assert facts['source_snapshot_matches'] and facts['strict_mtls'] and abs(facts['clock_offset_seconds'])<60
        common={'approval_kind':'USER_SINGLE_BILL_EXCEPTION','submission_id':sid,
                'provider':'current user explicit trial instruction 2026-10-22',
                'user_instruction':'先都忽略，然后推送一条数据测试；不用撤销，发送即可，后续没问题再启用'}
        entries=[
            ('EXT-03','OBSERVED','DMZ official HTTPS HEAD200 and worker strict mTLS health200; current preflight receipts'),
            ('EXT-04','DEFERRED_BY_USER','Formal hospital CA replacement deferred for this trial; existing pinned TEST-ONLY chain strictly validated; production CA NOT verified'),
            ('EXT-05','DEFERRED_WITH_CURRENT_CHECK','Sustained NTP deferred; current worker clock offset '+str(facts['clock_offset_seconds'])+' seconds vs official HTTPS Date; long-term sync NOT verified'),
            ('EXT-06','TO_BE_TESTED_BY_REQUEST','Existing AppKey/Secret/hospital identity retained; uploadretail permission UNKNOWN and to be established by this one response; no permission success claimed'),
            ('EXT-07','OBSERVED','Existing official SDK/signature vector verification and actual signed query accepted; source release unchanged'),
            ('EXT-08','OBSERVED_WITH_DEFERRED_CONFIRMATION','Existing approved r10-normal-20261022-r1 dictionaries, bill_type321, hospital mapping; written321/323 final confirmation deferred'),
            ('EXT-10','DEFERRED_BY_USER','Previous vendor upload status UNKNOWN; user explicitly waives confirmation for this selected one-bill test; no historical unreported claim'),
            ('EXT-12','AUTHORIZED_SINGLE_BILL','User explicitly requests one real bill now; exact frozen submission/config/window/pharmacy and max_bills1; no automatic batch authority'),
        ]
        items=[{**common,'key':key,'disposition':disposition,'evidence_ref':description}
               for key,disposition,description in entries]
        out['approval']=call('POST','/drug-trace/evidence',{
            'profile':'alihealth_yljg_retail_v1','capability':'send','destination_id':did,
            'items':items,'valid_until':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            'config_release_id':cfg,'scope':facts['scope']})
        out['destination']=call('POST',f'/drug-trace/destinations/{did}/activation',{'enabled':True})
    elif phase == 'send':
        out['send']=call('POST',f'/drug-trace/submissions/{sid}/send',{
            'confirm':True,'reason':'User explicitly authorizes exactly this one real trial and retention of configuration; production CA/NTP/written confirmations/history check deferred, max_bills=1'})
    elif phase == 'query':
        out['query']=call('POST',f'/drug-trace/submissions/{sid}/query')
    else:raise RuntimeError('unknown phase')
    out['ok']=True
except Exception as exc:
    out['error_type']=type(exc).__name__
print('__SOURCE_RESULT__'+json.dumps(out,ensure_ascii=False)+'__END__')
if not out['ok']:raise SystemExit(1)
