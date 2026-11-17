"""Independently cross-check original XLSX against the previous readonly SQLite.

Business identifiers stay in memory; shared output contains counts/digests only.
Never reruns the prior import or overwrites the historical database/workbook.
"""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import hashlib
import json
import os
import sqlite3
import time
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
PREVIOUS=ROOT/'docs/development/drug-trace-history-compare-20261023'
INPUT=ROOT/'数据上报/1、码上放心平台/历史上报数据'
PRIVATE=Path(os.environ['LOCALAPPDATA'])/'BLYB/history-compare-20261023'


def digest_row(stream,row):
    stream.update((json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf8'))


def main():
    started=time.monotonic()
    previous=json.loads((PREVIOUS/'history-summary.json').read_text('utf8'))
    database=PRIVATE/'history.sqlite3'
    db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    db.execute('PRAGMA query_only=ON')
    out={'captured_at':datetime.now(timezone.utc).isoformat(),'files':[],
         'originals_modified':False,'previous_database_modified':False,
         'business_keys_or_codes_returned':False,'live_queries':0,'uploads':0}
    issues=Counter();numeric=0;rows_total=0
    for meta in previous['files']:
        path=INPUT/meta['file']
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        book=load_workbook(path,read_only=True,data_only=True)
        source_sheets={};count=0
        for sheet in book:
            iterator=sheet.iter_rows()
            headers=[c.value for c in next(iterator)]
            if headers!=['单据号','单据时间','追溯码']:
                raise RuntimeError('UNEXPECTED_SOURCE_COLUMNS')
            stream=hashlib.sha256();sheet_rows=0
            for number,cells in enumerate(iterator,2):
                values=[c.value for c in cells]
                if all(v is None for v in values):continue
                bill,stamp,code=values
                bill='' if bill is None else str(bill)
                numeric+=int(isinstance(code,(int,float)))
                code='' if code is None else str(code)
                stamp=stamp.isoformat(sep=' ',timespec='seconds') if isinstance(stamp,datetime) else str(stamp or '')
                if len(code)!=20 or not code.isascii() or not code.isdecimal():issues['code_format']+=1
                if bill!=bill.strip() or code!=code.strip():issues['outer_whitespace']+=1
                digest_row(stream,(number,bill,stamp,code,cells[2].data_type))
                count+=1;sheet_rows+=1
            source_sheets[sheet.title]={'rows':sheet_rows,'digest':stream.hexdigest()}
        book.close()
        mirror={}
        for sheet,row_no,bill,stamp,code,code_type in db.execute('SELECT sheet,row_no,bill,bill_time,code,code_type FROM history WHERE file=? ORDER BY sheet,row_no',(meta['file'],)):
            if sheet not in mirror:mirror[sheet]={'rows':0,'stream':hashlib.sha256()}
            digest_row(mirror[sheet]['stream'],(row_no,bill,stamp,code,code_type));mirror[sheet]['rows']+=1
        mirror={key:{'rows':value['rows'],'digest':value['stream'].hexdigest()} for key,value in mirror.items()}
        after=hashlib.sha256(path.read_bytes()).hexdigest()
        out['files'].append({'file':path.name,'rows':count,'expected_rows':meta['rows'],
                             'source_matches_recorded_sha256':before==meta['sha256'],
                             'source_unchanged_during_audit':before==after,
                             'sqlite_row_content_and_locations_match':source_sheets==mirror})
        rows_total+=count
    scalar=lambda sql:db.execute(sql).fetchone()[0]
    actual={'rows':scalar('SELECT COUNT(*) FROM history'),
            'unique_bills':scalar('SELECT COUNT(DISTINCT bill) FROM history'),
            'unique_codes':scalar('SELECT COUNT(DISTINCT code) FROM history'),
            'unique_bill_code_pairs':scalar('SELECT COUNT(*) FROM (SELECT bill,code FROM history GROUP BY bill,code)'),
            'unique_exact_rows':scalar('SELECT COUNT(*) FROM (SELECT bill,bill_time,code FROM history GROUP BY bill,bill_time,code)'),
            'bills_with_multiple_times':scalar('SELECT COUNT(*) FROM (SELECT bill FROM history GROUP BY bill HAVING COUNT(DISTINCT bill_time)>1)'),
            'codes_with_multiple_bills':scalar('SELECT COUNT(*) FROM (SELECT code FROM history GROUP BY code HAVING COUNT(DISTINCT bill)>1)'),
            'bill_time_min':scalar('SELECT MIN(bill_time) FROM history'),
            'bill_time_max':scalar('SELECT MAX(bill_time) FROM history')}
    actual['repeated_bill_code_rows']=actual['rows']-actual['unique_bill_code_pairs']
    actual['repeated_exact_rows']=actual['rows']-actual['unique_exact_rows']
    actual['numeric_code_cells']=numeric
    days=[{'date':r[0],'rows':r[1],'bills':r[2],'codes':r[3]} for r in db.execute('SELECT SUBSTR(bill_time,1,10),COUNT(*),COUNT(DISTINCT bill),COUNT(DISTINCT code) FROM history GROUP BY SUBSTR(bill_time,1,10) ORDER BY 1')]
    months=[{'month':r[0],'rows':r[1],'bills':r[2]} for r in db.execute('SELECT SUBSTR(bill_time,1,7),COUNT(*),COUNT(DISTINCT bill) FROM history GROUP BY SUBSTR(bill_time,1,7) ORDER BY 1')]
    out['summary']=actual
    out['previous_summary_matches']={key:previous[key]==value for key,value in actual.items()}
    out['per_day_matches']=days==previous['per_day']
    out['per_month_matches']=months==previous['per_month']
    out['validation_issues']=dict(issues)
    out['rows_streamed_independently']=rows_total
    prior_workbook=PRIVATE/'历史上报数据_离线核对.xlsx'
    book=load_workbook(prior_workbook,read_only=True,data_only=True)
    actual_sheets={sheet.title:sheet.max_row-1 for sheet in book};book.close()
    prior_counts=json.loads((PREVIOUS/'offline-workbook-validation.json').read_text('utf8'))['sheets']
    out['workbook_sheet_counts_match']=actual_sheets==prior_counts
    out['workbook_sheet_counts']=actual_sheets
    out['passed']=all(all(row[k] for k in ('source_matches_recorded_sha256','source_unchanged_during_audit','sqlite_row_content_and_locations_match')) and row['rows']==row['expected_rows'] for row in out['files']) and all(out['previous_summary_matches'].values()) and out['per_day_matches'] and out['per_month_matches'] and not issues and out['workbook_sheet_counts_match']
    out['elapsed_seconds']=round(time.monotonic()-started,2)
    db.close()
    output=HERE/'offline-audit.json'
    with output.open('x',encoding='utf8') as f:json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps({key:value for key,value in out.items() if key not in ('files','workbook_sheet_counts')},ensure_ascii=False))
    return 0 if out['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
