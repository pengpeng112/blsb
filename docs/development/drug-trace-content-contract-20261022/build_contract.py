"""Build reviewable r10 contract/SQL from actual view definitions; no execution."""
from pathlib import Path
from datetime import datetime,timezone
import csv
import hashlib
import json
import re

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent

NORMAL_PREDICATE=("M.PRESC_SOURCE = 0 AND M.FLAG = 1 "
                  "AND M.DISPENSING_DATETIME IS NOT NULL "
                  "AND NVL(M.REFUND_FLAG, 0) = 0")
KIND_CASE="CASE WHEN "+NORMAL_PREDICATE+" THEN 'PATIENT_DISPENSE' ELSE 'UNKNOWN' END"
FINAL_CASE="CASE WHEN "+NORMAL_PREDICATE+" THEN 1 ELSE CAST(NULL AS NUMBER(1)) END"
VOID_CASE="CASE WHEN "+NORMAL_PREDICATE+" THEN 0 ELSE CAST(NULL AS NUMBER(1)) END"

def main():
    original=json.loads((ROOT/'docs/development/drug-trace-view-verify-20261022/evidence/reader-r1.json').read_text('utf-8'))
    views={r['VIEW_NAME']:r['TEXT'].strip() for r in original['queries']['view_definition']['rows']}
    current={};draft={}
    for name,text in views.items():
        current[name]=text
        text=text.replace("CAST('UNKNOWN' AS VARCHAR2(32)) AS business_kind",KIND_CASE+' AS business_kind')
        text=text.replace('CAST(NULL AS NUMBER(1)) AS finalized',FINAL_CASE+' AS finalized')
        text=text.replace('CAST(NULL AS NUMBER(1)) AS is_void',VOID_CASE+' AS is_void')
        text=text.replace('AS source_revision',"|| '_R10_RF_' || NVL(TO_CHAR(M.REFUND_FLAG), 'NULL') AS source_revision")
        draft[name]=text
    header='''-- r10 DRAFT: normal outpatient branch only, not approved or installed.
-- Review the documented status predicate with HIS/pharmacy before executing.
-- Execute as an authorized DBA in PL/SQL Developer, statement by statement.
-- Existing account/grants remain; this file creates NO user and sends NO data.
-- Explicit batch use only: late code edits/refunds still need a change source.
'''
    ddl=header+'\n\n'.join('CREATE OR REPLACE VIEW PHARMACY.'+name+' AS\n'+text+';\n' for name,text in draft.items())
    rollback='-- Exact previously observed view bodies; DBA-only rollback reference.\n'+'\n\n'.join(
        'CREATE OR REPLACE VIEW PHARMACY.'+name+' AS\n'+text+';\n' for name,text in current.items())
    (HERE/'sql/draft_normal_outpatient_views_r10.sql').write_text(ddl,'utf-8')
    (HERE/'sql/rollback_to_observed_views.sql').write_text(rollback,'utf-8')
    for name,text in draft.items():
        select=re.sub(r'\s+WITH READ ONLY\s*$','',text,flags=re.I)
        select+=(' AND 1=0' if name=='V_DRUG_TRACE_CODE' else ' WHERE 1=0')
        (HERE/'sql'/('parse_'+name.lower()+'.sql')).write_text(select+'\n','utf-8')
    contract={
        'revision':'drugtrace-upload-content-20261022-r10',
        'status':'DRAFT_FOR_HOSPITAL_REVIEW','runtime_approved':False,'installed':False,
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'source':{'host':'<his-host>','port':1521,'service_name':'his',
                  'reader':'BLYB_DRUGTRACE','views':list('PHARMACY.'+x for x in views),
                  'credential_ref':'.ai/private/drug-trace-view-verify-20261022/his-reader.credential.clixml'},
        'profile':'alihealth_yljg_retail_v1',
        'method':'alibaba.alihealth.drugtrace.top.yljg.uploadretail',
        'required_business_form_fields':{
            'bill_code':{'origin':'existing ensure_bill_code mapping, not LISTAGG(PRESC_DETAILID)',
                         'rule':'DTRT- plus existing composite-key SHA1 prefix; persistent mapping wins',
                         'business_key':['source_system','pharmacy_code','business_kind','source_bill_id'],
                         'source_bill_id':'full-second PRESC_DATE|PRESC_NO|DISPENSARY',
                         'legacy_bill_code_compatibility':'UNCONFIRMED; no historical replay'},
            'bill_time':{'origin':'PHARMACY.DRUG_PRESC_MASTER.DISPENSING_DATETIME',
                         'format':'YYYY-MM-DD HH24:MI:SS','timezone':'Asia/Shanghai business wall-clock',
                         'scan_create_time_is_same_field':False},
            'bill_type':{'proposed_value':'321','basis':'existing approved official-202601-r1 dictionary and 2026.01 guide',
                         'official_api_description_conflict':'API page description also says 323; not independently resolved'},
            'ref_user_id':{'origin':'already recorded hospital ref_ent_id, not ent_id and not HIS ORG_CODE',
                           'value_ref':'.ai/private/alihealth-drugtrace-20261020/profile.json#ref_ent_id',
                           'requires_pharmacy_mapping':True},
            'trace_codes':{'origin':'DRUG_USER.MED_TRACE_RECORD.T_TRACE_CODE',
                           'wire_format':'comma-joined original string values for the whole bill',
                           'record_identity':'RECORD_ID VARCHAR2(32)',
                           'no_numeric_cast':True,'no_distinct_or_max_repair':True,
                           'same_bill_duplicates':'BLOCK','parent_child_codes_in_same_bill':'BLOCK',
                           'max_code_count_guard':'retain current configured guard; official documentation governs'}},
        'optional_not_required_for_minimum_retail':['physic_type','from_user_id','oper_ic_code','oper_ic_name','customer_id_type','customer_id'],
        'internal_not_form_parameters':['quantity','unit','code_level','patient_ref','encounter_ref','expected_code_count'],
        'patient_fields_policy':'omit from minimal retail upload',
        'normal_outpatient_candidate':{'predicate':NORMAL_PREDICATE,'business_kind':'PATIENT_DISPENSE',
                                        'finalized':1,'is_void':0,'approval_status':'NOT_APPROVED',
                                        'all_other_states':{'business_kind':'UNKNOWN','finalized':None,'is_void':None},
                                        'source_comment_evidence':'evidence/his-content-r1.json',
                                        'documented_source_values':{'PRESC_SOURCE':{'0':'outpatient','1':'inpatient'},
                                            'FLAG':{'1':'normal dispensing','2':'full return','3':'partial return'},
                                            'REFUND_FLAG':{'1':'refunded','0':'not refunded','NULL':'not refunded'}}},
        'join':{'conditions':['T.TRACE_BILLNO=TO_CHAR(M.PRESC_NO)','T.PRESC_DATE=M.PRESC_DATE','T.STORE_CODE=M.DISPENSARY'],
                 'qualification':'bounded live sample evidence, not a published formal relation',
                 'presc_detailid_equals_legacy_item_no':False},
        'dictionary_draft':[
            {'namespace':'drugtrace.bill_type','source_code':'PRESC_SOURCE=0;FLAG=1','target':'321','approve':False},
            {'namespace':'drugtrace.query_bill_type','source_code':'PRESC_SOURCE=0;FLAG=1','target':'A','approve':False},
            {'namespace':'drugtrace.ref_ent_id','source_code':'explicit selected pharmacy code','target_ref':'existing hospital ref_ent_id','approve':False}],
        'collection_mode':'batch','continuous_collection_approved':False,
        'first_bill_scope':{'max_bills':1,'entire_bill_required':True,
                            'proposed_initial_subset':'normal outpatient, no refund, all selected active rows IS_DELETED=0 and SPLIT_FLAG=0; unknowns wait',
                            'unreported_status':'not determinable from TRACE_STATUS; requires legacy/platform reconciliation',
                            'specific_bill_selected':False},
        'deployment_remaining':['deploy previously tested Oracle NULL-cursor fix','securely bind new reader to worker',
            'approve source/status contract','publish source and correct dictionary revisions','enable source/destination normally',
            'close real send evidence/scope and allow required DMZ upload method','one-bill send and terminal/each-code verification'],
        'real_uploads':0}
    (HERE/'upload-content-contract-r10.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2)+'\n','utf-8')
    asset_base=Path('F:/python/数据资产/开发起步包/数据资产_HIS源端资产包')
    catalog=json.loads((asset_base/'his_source_catalog.json').read_text('utf-8-sig'))
    evidence={'asset_meta':catalog['meta'],'manifest_present':(asset_base/'manifest.json').exists(),
              'source_artifacts':[],'target_columns':[],'relevant_legacy_formal_relations':[]}
    for file in (asset_base/'his_source_columns.csv',asset_base/'his_source_relationships.csv',
        Path('F:/python/数据资产/开发起步包/系统表结构/HIS系统数据库表结构.md'),
        Path('F:/python/数据资产/开发起步包/系统表结构/HIS系统药房药库表设计文档.md')):
        evidence['source_artifacts'].append({'path':str(file),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
    with (asset_base/'his_source_columns.csv').open(encoding='utf-8-sig',newline='') as stream:
        evidence['target_columns']=[row for row in csv.DictReader(stream) if (row['source_owner'],row['table_name']) in
            {('DRUG_USER','MED_TRACE_RECORD'),('PHARMACY','DRUG_PRESC_MASTER'),('PHARMACY','DRUG_PRESC_DETAIL')}]
    with (asset_base/'his_source_relationships.csv').open(encoding='utf-8-sig',newline='') as stream:
        evidence['relevant_legacy_formal_relations']=[row for row in csv.DictReader(stream) if
            'MED_TRACE_RECORD' in row['from_table'] or 'MED_TRACE_RECORD' in row['to_table']]
    (HERE/'evidence/local-asset-extract.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n','utf-8')
    print('Built DRAFT r10 contract, proposed view DDL, observed-view rollback and zero-row parse SELECTs. No DB execution.')

if __name__=='__main__':main()
