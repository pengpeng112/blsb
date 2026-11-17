"""Create a local restricted review workbook; no source DB connections."""
from pathlib import Path
from collections import Counter
from datetime import datetime
import json
import os
import sqlite3
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

HERE = Path(__file__).resolve().parent
PRIVATE = Path(os.environ['LOCALAPPDATA'])/'BLYB/history-compare-20261023'


def add_sheet(book,name,headers,rows):
    sheet=book.create_sheet(name)
    sheet.append(headers)
    for row in rows:
        sheet.append(list(row))
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value,str):cell.number_format='@'
    sheet.freeze_panes='A2'
    sheet.auto_filter.ref=sheet.dimensions
    for cell in sheet[1]:
        cell.font=Font(bold=True,color='FFFFFF')
        cell.fill=PatternFill('solid',fgColor='24476A')
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width=min(60,max(16,max(len(str(c.value or '')) for c in column[:200])+2))
    return sheet


def main():
    data=json.loads((HERE/'history-summary.json').read_text(encoding='utf-8'))
    db=sqlite3.connect(f'file:{(PRIVATE/"history.sqlite3").as_posix()}?mode=ro',uri=True)
    book=Workbook();book.remove(book.active)
    summary=[('核对阶段','仅历史文件离线检查，尚未连接视图，不能判定两边一致'),
      ('来源文件数',len(data['files'])),('原始行数',data['rows']),('历史单据号数',data['unique_bills']),
      ('不同追溯码',data['unique_codes']),('最早文件单据时间',data['bill_time_min']),('最晚文件单据时间',data['bill_time_max']),
      ('完全重复行',data['repeated_exact_rows']),('同单号同码多余出现次数',data['repeated_bill_code_rows']),
      ('同单号不同时间的单号数',data['bills_with_multiple_times']),('同码多个单号的码数',data['codes_with_multiple_bills']),
      ('码被Excel按数值存储',data['numeric_code_cells']),('注意','同码多单、同单同码再次出现不直接判定错报；待结合业务确认'),
      ('时间口径','文件单据时间不能直接等同现视图发药时间'),('平台回执','文件没有成功/失败或平台回执字段；本次按用户说明作为既往上报清单')]
    add_sheet(book,'核对说明',['项目','结果'],summary)
    add_sheet(book,'逐文件',['文件','条数','文件字节','SHA256'],[(r['file'],r['rows'],r['bytes'],r['sha256']) for r in data['files']])
    add_sheet(book,'逐日分布',['日期','原始条数','历史单据数','不同码数'],[(r['date'],r['rows'],r['bills'],r['codes']) for r in data['per_day']])
    query='''SELECT h.file,h.sheet,h.row_no,h.bill,h.bill_time,h.code FROM history h
        JOIN (SELECT bill,code FROM history GROUP BY bill,code HAVING COUNT(*)>1) d
        ON h.bill=d.bill AND h.code=d.code ORDER BY h.bill,h.code,h.bill_time'''
    add_sheet(book,'同单同码再次出现',['文件','工作表','原始行号','历史单号','文件单据时间','追溯码'],db.execute(query))
    query='''SELECT h.file,h.sheet,h.row_no,h.bill,h.bill_time,h.code FROM history h
        JOIN (SELECT bill FROM history GROUP BY bill HAVING COUNT(DISTINCT bill_time)>1) d
        ON h.bill=d.bill ORDER BY h.bill,h.bill_time,h.code'''
    add_sheet(book,'同单号多个时间',['文件','工作表','原始行号','历史单号','文件单据时间','追溯码'],db.execute(query))
    query='''SELECT code,COUNT(*),COUNT(DISTINCT bill),MIN(bill_time),MAX(bill_time)
        FROM history GROUP BY code HAVING COUNT(DISTINCT bill)>1 ORDER BY 3 DESC,code'''
    add_sheet(book,'同码跨单待核',['追溯码','出现次数','涉及历史单号数','最早时间','最晚时间'],db.execute(query))
    prefix_dates=Counter();delta_days=Counter();fail=0
    for bill,stamp in db.execute('SELECT bill,bill_time FROM history'):
        try:
            prefix=datetime.strptime(bill[:14],'%Y%m%d%H%M%S')
            actual=datetime.fromisoformat(stamp)
            prefix_dates[prefix.date().isoformat()]+=1
            delta_days[(actual.date()-prefix.date()).days]+=1
        except ValueError:fail+=1
    extra={'prefix_parse_failures':fail,'bill_numeric_prefix_dates':dict(sorted(prefix_dates.items())),
           'file_time_minus_bill_prefix_date_days':dict(sorted(delta_days.items())),
           'prefix_meaning':'string/date pattern only; not a verified HIS mapping'}
    (HERE/'history-key-patterns.json').write_text(json.dumps(extra,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    output=PRIVATE/'历史上报数据_离线核对.xlsx';book.save(output)
    check=load_workbook(output,read_only=True,data_only=True)
    counts={s.title:s.max_row-1 for s in check};check.close()
    result={'workbook':str(output),'sheets':counts,'raw_data_in_shared_docs':False,'live_queries':0,
            'prefix_parse_failures':fail,'prefix_date_min':min(prefix_dates),'prefix_date_max':max(prefix_dates),
            'file_time_minus_prefix_date_days':dict(sorted(delta_days.items()))}
    (HERE/'offline-workbook-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
