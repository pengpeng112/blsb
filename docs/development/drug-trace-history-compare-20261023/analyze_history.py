"""Read original historical XLSX files; keep raw identifiers in a private local SQLite DB."""
from pathlib import Path
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
import re
import sqlite3
import time
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
INPUT = ROOT/'数据上报/1、码上放心平台/历史上报数据'
PRIVATE = Path(os.environ['LOCALAPPDATA'])/'BLYB/history-compare-20261023'


def main():
    started = time.monotonic()
    db = sqlite3.connect(PRIVATE/'history.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS history(file TEXT,sheet TEXT,row_no INTEGER,bill TEXT,bill_time TEXT,code TEXT,code_type TEXT)')
    if db.execute('SELECT COUNT(*) FROM history').fetchone()[0]:
        raise RuntimeError('Existing analysis DB: use a new revision rather than overwrite')
    files = []
    errors = Counter()
    numeric_codes = 0
    patterns = Counter()
    for path in sorted(INPUT.glob('*.xlsx'),key=lambda p:int(p.stem.split('_')[-1])):
        book = load_workbook(path,read_only=True,data_only=True)
        count = 0
        for sheet in book:
            stream = sheet.iter_rows()
            headers = [c.value for c in next(stream)]
            if headers != ['单据号','单据时间','追溯码']:
                raise ValueError('Unexpected columns in '+path.name)
            batch = []
            for row_no,row in enumerate(stream,2):
                bill,stamp,code = [c.value for c in row]
                if all(x is None for x in (bill,stamp,code)):
                    continue
                if isinstance(code,(int,float)):
                    numeric_codes += 1
                if not isinstance(stamp,datetime):
                    errors['non_datetime'] += 1
                    stamp_text = '' if stamp is None else str(stamp)
                else:
                    stamp_text = stamp.isoformat(sep=' ',timespec='seconds')
                bill_text = '' if bill is None else str(bill)
                code_text = '' if code is None else str(code)
                if not re.fullmatch(r'[0-9]{20}',code_text):
                    errors['code_not_20_ascii_digits'] += 1
                if bill_text != bill_text.strip() or code_text != code_text.strip():
                    errors['identifier_outer_whitespace'] += 1
                if not bill_text: errors['empty_bill'] += 1
                patterns[re.sub(r'[0-9]','D',bill_text)] += 1
                batch.append((path.name,sheet.title,row_no,bill_text,stamp_text,code_text,row[2].data_type))
                count += 1
                if len(batch)>=1000:
                    db.executemany('INSERT INTO history VALUES (?,?,?,?,?,?,?)',batch);batch=[]
            if batch:db.executemany('INSERT INTO history VALUES (?,?,?,?,?,?,?)',batch)
        db.commit()
        book.close()
        files.append({'file':path.name,'rows':count,'bytes':path.stat().st_size,
                      'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    db.execute('CREATE INDEX history_bill_time ON history(bill_time,bill)')
    db.execute('CREATE INDEX history_code ON history(code)')
    db.execute('CREATE INDEX history_bill_code ON history(bill,code)')
    db.commit()
    scalar=lambda sql:db.execute(sql).fetchone()[0]
    report={
        'files':files,'rows':scalar('SELECT COUNT(*) FROM history'),
        'unique_bills':scalar('SELECT COUNT(DISTINCT bill) FROM history'),
        'unique_codes':scalar('SELECT COUNT(DISTINCT code) FROM history'),
        'unique_bill_code_pairs':scalar('SELECT COUNT(*) FROM (SELECT bill,code FROM history GROUP BY bill,code)'),
        'unique_exact_rows':scalar('SELECT COUNT(*) FROM (SELECT bill,bill_time,code FROM history GROUP BY bill,bill_time,code)'),
        'bill_time_min':scalar('SELECT MIN(bill_time) FROM history'),
        'bill_time_max':scalar('SELECT MAX(bill_time) FROM history'),
        'bills_with_multiple_times':scalar('SELECT COUNT(*) FROM (SELECT bill FROM history GROUP BY bill HAVING COUNT(DISTINCT bill_time)>1)'),
        'codes_with_multiple_bills':scalar('SELECT COUNT(*) FROM (SELECT code FROM history GROUP BY code HAVING COUNT(DISTINCT bill)>1)'),
        'numeric_code_cells':numeric_codes,'validation_issues':dict(errors),'bill_shapes':dict(patterns),
        'per_day':[{'date':r[0],'rows':r[1],'bills':r[2],'codes':r[3]} for r in db.execute('SELECT SUBSTR(bill_time,1,10),COUNT(*),COUNT(DISTINCT bill),COUNT(DISTINCT code) FROM history GROUP BY SUBSTR(bill_time,1,10) ORDER BY 1')],
        'per_month':[{'month':r[0],'rows':r[1],'bills':r[2]} for r in db.execute('SELECT SUBSTR(bill_time,1,7),COUNT(*),COUNT(DISTINCT bill) FROM history GROUP BY SUBSTR(bill_time,1,7) ORDER BY 1')],
        'elapsed_seconds':round(time.monotonic()-started,2),'source_files_changed':False,'live_queries':0,
    }
    report['repeated_exact_rows']=report['rows']-report['unique_exact_rows']
    report['repeated_bill_code_rows']=report['rows']-report['unique_bill_code_pairs']
    (HERE/'history-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    db.close()
    print(json.dumps({k:v for k,v in report.items() if k not in ('files','per_day')},ensure_ascii=False))


if __name__=='__main__':
    main()
