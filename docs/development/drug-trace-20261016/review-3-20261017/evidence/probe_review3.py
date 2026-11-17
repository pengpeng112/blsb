"""Independent offline FIX2 review. Network and credential reads forbidden."""
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util, json, socket, sys, tempfile

ROOT = Path(__file__).resolve().parents[5]
IMPL = ROOT/'implementation'
sys.path.insert(0, str(IMPL))
import _bootstrap
from domains.drug_trace import service as S, contracts as C, payload as PL, ingest as I
from destinations.alihealth_drugtrace import protocol as P
from core_db.models import Submission, Job, SourceCheckpoint, ConfigRevision, PackageRevision
from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision, DrugTraceCollectionRun
from contracts import new_uuid, SubmissionState

observations = []
def note(key, reproduced, detail):
    observations.append({'id':key,'issue_reproduced':bool(reproduced),'detail':detail})

def main():
    spec = importlib.util.spec_from_file_location('fix2_fixture', IMPL/'tests/integration/test_drug_trace_fix2.py')
    F = importlib.util.module_from_spec(spec); spec.loader.exec_module(F)
    with tempfile.TemporaryDirectory(prefix='blyb-review3-') as tmp:
        gen = F.env.__wrapped__(Path(tmp)); env = next(gen)
        try:
            # Confirm old critical bugs actually changed, using real current code.
            partial = S._classify_query({'found':True,'bills':{'list':[{
                'bill_code':'SYN','result_type':'3','sub_process_flag':'52'}]}}, 'SYN')
            pos = {'partial_now_PARTIAL':partial['state']=='PARTIAL',
                   'official_upload_now_ACCEPTED':P.parse_response(C.METHOD_UPLOAD_RETAIL,200,
                    json.dumps({P.method_response_key(C.METHOD_UPLOAD_RETAIL):{'response_success':True,'model':'SYN'}}).encode())['outcome']=='ACCEPTED',
                   'real_signature_still_disabled':P.SIGNATURE_VERIFIED is False}
            f = F._frozen_retail(env)
            with env['sf']() as db:
                sub = db.get(Submission,f['submission']['submission_id'])
                qbefore = db.query(Job).filter_by(job_type=C.DomainJobType.QUERY).count()
                S.perform_send(db,env['tenant_id'],sub)
                qafter = db.query(Job).filter_by(job_type=C.DomainJobType.QUERY).count()
                note('R3-03-accepted',sub.state==SubmissionState.ACCEPTED and qafter==qbefore,
                     {'state':sub.state,'new_query_jobs':qafter-qbefore})

                query_calls=[]
                class ErrorClient:
                    def query_bill_status(self,biz):
                        query_calls.append(dict(biz))
                        return {'found':None,'unknown':False,'bills':{'list':[]},
                                'error':{'code':'SYN-AUTH','msg':'synthetic permission denied'}}
                failed = S.perform_query(db,env['tenant_id'],sub,client=ErrorClient())
                note('R3-03-error',failed['classification']['state']=='NOT_FOUND',
                     {'permission_error_classification':failed['classification']})
                note('R3-01-query',query_calls[0]['bill_type'] not in ('A','AI','AO'),
                     {'actual_query_bill_type':query_calls[0]['bill_type'],'documented_selector_values':['A','AI','AO']})

                class BoundedFullClient:
                    def __init__(self):self.pages=[]
                    def query_bill_status(self,biz):
                        self.pages.append(biz['page'])
                        if len(self.pages)>3:raise RuntimeError('review stop: repeated page safeguard')
                        return {'found':True,'unknown':False,'total_num':None,'bills':{'list':[
                            {'bill_code':f'SYN-{i}','result_type':'1','sub_process_flag':''}
                            for i in range(S.QUERY_PAGE_SIZE)]}}
                full=BoundedFullClient()
                try:S.perform_query(db,env['tenant_id'],sub,client=full)
                except RuntimeError:db.rollback()
                note('R3-03-page',full.pages==['1']*4,{'requested_pages_before_guard':full.pages})

                scope = {'pharmacies':['OTHER-PHARMACY'],'max_bills':0}
                try:S._check_send_scope(scope,{'pharmacy_code':'YF01','bill_time':'2026-10-12T08:00:00'}); allowed=True
                except Exception:allowed=False
                note('R3-04-scope',allowed,{'excluded_pharmacy_and_zero_quota_allowed':allowed})
                ev=S.approve_evidence(db,env['tenant_id'],profile=C.PROFILE_RETAIL,capability='send',
                    destination_id=sub.destination_id,provided_items=[{'key':x['key']} for x in S.REQUIRED_EVIDENCE['send']],
                    approved_by='synthetic',config_release_id='',scope={})
                db.commit()
                status=S.evidence_state(db,env['tenant_id'],profile=C.PROFILE_RETAIL,capability='send',
                    destination_id=sub.destination_id,config_release_id='NEW')
                note('R3-04-config',status['status']=='APPROVED',{'blank_evidence_vs_nonblank_release':status['status']})

                # Inventory payload construction needs a hospital ent_id distinct from ref_ent_id.
                mapping={'drugtrace.bill_type':'102','drugtrace.ref_ent_id':'HOSP-REF',
                         'drugtrace.ref_user_id':'HOSP-REF','drugtrace.supplier_user_id':'SUPPLIER-ENT',
                         'drugtrace.physic_type':'3','drugtrace.client_type':'2','drugtrace.ent_id':'HOSP-ENT'}
                def lookup(db,tenant,ns,source,revs):revs[ns]='SYN';return mapping[ns]
                h=F._bill_row('INV',kind='SUPPLIER_IN',supplier_code='S1')
                rev=SimpleNamespace(quality_state='VALID',header=h,lines=[F._code_row('INV',kind='SUPPLIER_IN')])
                bill=SimpleNamespace(business_kind='SUPPLIER_IN')
                with patch.object(PL,'lookup_mapping_rev',side_effect=lookup),patch.object(PL,'ensure_bill_code',return_value='SYN-INV'):
                    inv=PL.build_payload(db,env['tenant_id'],rev,bill,profile=C.PROFILE_INOUT)
                note('R3-01-id',inv['to_user_id']=='HOSP-REF',
                     {'upload_ref_user_id':inv['ref_user_id'],'purchase_to_user_id':inv['to_user_id'],'expected_hospital_ent_id':'HOSP-ENT'})

                # Re-prepare after config drift never yields a new usable submission/config.
                dest=db.get(__import__('core_db.models',fromlist=['Destination']).Destination,sub.destination_id)
                cr=ConfigRevision(id=new_uuid(),tenant_id=env['tenant_id'],config_type='connector',
                                  owner_id=dest.id,revision=1,content_json={'app_key':'synthetic'},
                                  digest='synthetic',state='PUBLISHED')
                db.add(cr);db.flush();dest.config_revision=cr.id;db.commit()
                # Separate genuinely PREPARED, never-sent submission pinned to config1.
                config_conn=F._connector_id(env)
                config_wh=F._wh_with([F._bill_row('CONFIG-U',source_bill_kind_raw='323',
                    bill_time='2026-10-11T07:55:00')],[F._code_row('CONFIG-U')])
                S.run_collection(db,env['tenant_id'],config_conn,config_wh,
                    window_begin=datetime(2026,10,12),window_end=datetime(2026,10,13))
                config_bill=db.query(DrugTraceBill).filter_by(connector_id=config_conn).one()
                old_prep=S.prepare_submission(db,env['tenant_id'],config_bill.current_revision_id,
                    profile=C.PROFILE_RETAIL,destination_id=dest.id,config_release_id=cr.id)
                config2=ConfigRevision(id=new_uuid(),tenant_id=env['tenant_id'],config_type='connector',
                    owner_id=dest.id,revision=2,content_json={'app_key':'synthetic-new'},digest='synthetic2',state='PUBLISHED')
                db.add(config2);db.flush();dest.config_revision=config2.id;db.commit()
                prepared=S.prepare_submission(db,env['tenant_id'],config_bill.current_revision_id,profile=C.PROFILE_RETAIL,
                                               destination_id=dest.id,config_release_id=config2.id)
                pinned=(db.get(PackageRevision,prepared['package_revision_id']).provenance or {}).get('config_release_id')
                note('R3-06-reprepare',prepared['submission_id']==old_prep['submission_id'] and pinned!=config2.id,
                     {'same_old_submission':prepared['submission_id']==old_prep['submission_id'],
                      'state':'PREPARED_never_sent','pinned_config_unchanged':pinned==cr.id})
                from core_db.models import Tenant, DictionaryRevision
                other_tenant=Tenant(id=new_uuid(),code='synthetic-other',name='Synthetic Other')
                db.add(other_tenant);db.flush()
                foreign=ConfigRevision(id=new_uuid(),tenant_id=other_tenant.id,config_type='connector',
                    owner_id='foreign-owner',revision=1,content_json={'app_key':'FOREIGN-SYNTHETIC','app_secret_ref':'mock:fixture'},
                    digest='synthetic-foreign',state='DRAFT')
                db.add(foreign);db.flush()
                foreign_client=S.build_client(db,env['tenant_id'],dest,config_release_id=foreign.id)
                note('R3-06-config-owner',foreign_client.app_key=='FOREIGN-SYNTHETIC',
                     {'cross_tenant_DRAFT_config_was_loaded':foreign_client.app_key=='FOREIGN-SYNTHETIC'})
                older=DictionaryRevision(id=new_uuid(),tenant_id=env['tenant_id'],namespace='synthetic.audit',
                    version='1',items=[{'code':'X','target':'OLD-APPROVED'}],source='target',approved_by='synthetic',
                    created_at=datetime(2026,1,1))
                newer=DictionaryRevision(id=new_uuid(),tenant_id=env['tenant_id'],namespace='synthetic.audit',
                    version='2',items=[{'code':'X','target':'NEW-DRAFT'}],source='target',approved_by=None,
                    created_at=datetime(2026,2,1))
                db.add_all([older,newer]);db.flush(); revisions={}
                value=PL.lookup_mapping_rev(db,env['tenant_id'],'synthetic.audit','X',revisions)
                note('R3-06-dict-evidence',value=='OLD-APPROVED' and revisions['synthetic.audit']==newer.id,
                     {'value_from_approved_old':value,'recorded_revision_points_to_draft':revisions['synthetic.audit']==newer.id})

                # Reconciliation only asks page 1 and drops required query dates.
                S.approve_evidence(db,env['tenant_id'],profile=C.PROFILE_RETAIL,capability='reconcile',
                    destination_id=dest.id,provided_items=[{'key':'EXT-09'}],approved_by='synthetic')
                sub.state=SubmissionState.CONFIRMED;db.commit()
                class ReconClient:
                    def __init__(self):self.calls=[]
                    def query_bill_status(self,biz):
                        self.calls.append(biz)
                        return {'found':True,'unknown':False,'total_num':2,'bills':{'list':[{
                            'bill_code':f['bill_code'],'result_type':'3','sub_process_flag':'51'}]}}
                rc=ReconClient()
                recon=S.run_reconciliation(db,env['tenant_id'],destination_id=dest.id,profile=C.PROFILE_RETAIL,
                    window_begin=datetime(2026,10,12),window_end=datetime(2026,10,13),client=rc)
                note('R3-07-reconcile',len(rc.calls)==1 and recon['summary']['matched']==1,
                    {'pages_read':len(rc.calls),'missing_required_query_dates':not {'begin_date','end_date'}<=set(rc.calls[0]),
                     'matched_despite_total2_only1_observed':recon['summary']['matched']})

            # Demonstrate residual tuple-order versus concatenation-order disagreement.
            a=('HIS','YF1','PATIENT_DISPENSE','1');b=('HIS','YF10','PATIENT_DISPENSE','1')
            note('R3-02-cursor',a<b and '|'.join(a)>'|'.join(b),
                 {'ORDER_BY_tuple_A_before_B':a<b,'SQL_concatenation_B_after_cursor_A':'|'.join(b)>'|'.join(a),
                  'scope':'predicate counterexample, not a live Oracle query'})

            # Independent session pause must be visible to the running collector.
            conn=F._connector_id(env)
            wh=F._wh_with([F._bill_row('P1'),F._bill_row('P2')],
                          [F._code_row('P1',record_id='P1'),F._code_row('P2',record_id='P2')])
            with env['sf']() as db:
                run=DrugTraceCollectionRun(id=new_uuid(),tenant_id=env['tenant_id'],connector_id=conn,
                    stream=I.STREAM,profile=C.PROFILE_RETAIL,state='PENDING',
                    window_begin=datetime(2026,10,12),window_end=datetime(2026,10,13))
                db.add(run);db.commit();rid=run.id
                fetch=wh.fetch_all;paused=[]
                def interrupt(template,params,limit,**kw):
                    result=fetch(template,params,limit,**kw)
                    if template=='drugtrace.poll_bills' and not paused:
                        with env['sf']() as other:
                            target=other.get(DrugTraceCollectionRun,rid);target.state='PAUSED';other.commit()
                        paused.append(True)
                    return result
                wh.fetch_all=interrupt
                summary=S.run_collection(db,env['tenant_id'],conn,wh,run_id=rid,
                    window_begin=datetime(2026,10,12),window_end=datetime(2026,10,13),page_limit=1)
                note('R3-05-pause',run.state=='DONE',{'independent_session_PAUSED_then_collector_state':run.state,
                     'bills_seen':summary.get('bills_seen')})

            # A page error after one successful page persists snapshots without checkpoint.
            conn2=F._connector_id(env)
            wh2=F._wh_with([F._bill_row('A1')],[F._code_row('A1')]);fetch2=wh2.fetch_all;seen=[]
            def fail_after_page(template,params,limit,**kw):
                if template=='drugtrace.poll_bills':
                    seen.append(True)
                    if len(seen)>1:raise RuntimeError('synthetic second page failure')
                return fetch2(template,params,limit,**kw)
            wh2.fetch_all=fail_after_page
            with env['sf']() as db:
                try:S.run_collection(db,env['tenant_id'],conn2,wh2,window_begin=datetime(2026,10,12),
                    window_end=datetime(2026,10,13),page_limit=1)
                except RuntimeError:pass
            with env['sf']() as db:
                bills=db.query(DrugTraceBill).filter_by(connector_id=conn2).count()
                cps=db.query(SourceCheckpoint).filter_by(connector_id=conn2).count()
                note('R3-05-atomic',bills==1 and cps==0,{'persisted_bills_after_failed_run':bills,'checkpoints':cps})
        finally:
            try:next(gen)
            except StopIteration:pass
    return pos

if __name__=='__main__':
    with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
        positives=main()
    report={'at_utc':datetime.now(timezone.utc).isoformat(),'mode':'offline_review_no_credentials_no_network',
            'confirmed_fixes':positives,'observations':observations}
    Path(__file__).with_name('probe-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if all(x['issue_reproduced'] for x in observations) and all(positives.values()) else 2)
