"""Create a new private review workbook, preserving original files and prior notes."""
from pathlib import Path
from datetime import datetime
import json
import os
import sqlite3
from openpyxl import Workbook,load_workbook
from openpyxl.styles import Font,PatternFill

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent


def sheet(book,name,headers,rows):
    ws=book.create_sheet(name);ws.append(headers)
    for row in rows:
        ws.append(list(row))
        for cell in ws[ws.max_row]:
            if isinstance(cell.value,str):cell.number_format='@'
    ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
    for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='24476A')
    for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=28
    return ws


def main():
    full=json.loads((HERE/'evidence/full-compare-r1.json').read_text('utf8'))
    diag=json.loads((HERE/'evidence/diagnose-r1.json').read_text('utf8'))
    details=json.loads(Path(full['private_details_path']).read_text('utf8'))
    states=json.loads(Path(diag['private_details_path']).read_text('utf8'))
    output=Path(full['private_details_path']).parent/'历史上报与视图比对_20261023_r1.xlsx'
    if output.exists():raise RuntimeError('EXISTING_WORKBOOK_MUST_BE_PRESERVED')
    original=Path(os.environ['LOCALAPPDATA'])/'BLYB/history-compare-20261023/history.sqlite3'
    db=sqlite3.connect(original.as_uri()+'?mode=ro',uri=True)
    book=Workbook();book.remove(book.active)
    s=full['summary'];extra=json.loads((HERE/'summary-extra.json').read_text('utf8'))
    rows=[('核查依据','2026-10-23用户明确批准的白天只读比对；全部历史码/明细号及出现次数'),
          ('历史行数',489613),('已找到对应行且记录次数相同',484536),('未匹配历史行',5077),
          ('未匹配不同旧单号',extra['missing_distinct_legacy_bills']),('未匹配不同码',531),
          ('匹配但当前UNKNOWN',1396),('UNKNOWN关联当前主单',411),('同单同码两次出现组数',31),
          ('同一批码的其他明细记录',868),('特别说明','未匹配不等于未报或必需补报；原码在别单可见也不代表本条已报'),
          ('时间口径','扫码时间、发药时间不同；未做相同CREATE_TIME区间的全视图反向扫描'),
          ('当前UNKNOWN原因','FLAG=2全退原始状态1350条；FLAG=1且REFUND_FLAG=1另46条，不强行改为正常上传'),
          ('范围','没有修改HIS、没有新增上报，也没有独立查平台逐码终态'),
          ('保留记录','本工作簿新建r1；既有离线工作簿与原Excel均不覆盖')]
    sheet(book,'核查说明',['项目','结果/边界'],rows)
    loc=lambda bill,code:db.execute('SELECT file,sheet,row_no,bill,bill_time,code FROM history WHERE bill=? AND code=? ORDER BY file,row_no',(bill,code)).fetchall()
    missing=[]
    for item in states['missing_annotated']:
        classification='原码仅在其他明细可见' if item['current_code_exists'] else '本次两视图未见该码'
        for row in loc(item['bill'],item['code']):
            missing.append((*row,classification,item['current_other_line_rows'],json.dumps(item['current_line_shapes'],ensure_ascii=False),'需DBA查原扫码记录及三项主单关联；不直接补报'))
    sheet(book,'视图未匹配5077',['原文件','工作表','原行号','历史单号','历史单据时间','原追溯码','当前查询分类','同码在其他行的记录数','当前其他行单号形状','处理建议'],missing)
    excluded=[]
    for item in states['excluded_states']:
        for row in loc(item['bill'],item['code']):excluded.append((*row,item['raw_kind'],item['refund_raw'],item['finalized_raw'],item['void_raw'],'当前退款/全退状态不属正常门诊发送分支；不把旧OUT直接改116'))
    sheet(book,'当前UNKNOWN1396',['原文件','工作表','原行号','历史单号','历史单据时间','原追溯码','主单原始分类状态','退费原始值','finalized原值','is_void原值','处理建议'],excluded)
    repeated=[]
    for bill,code,n in db.execute('SELECT bill,code,COUNT(*) FROM history GROUP BY bill,code HAVING COUNT(*)>1'):
        for row in loc(bill,code):repeated.append((*row,n,2,'不同扫码记录确实存在；先查拆零/重扫/更正原因，不用DISTINCT掩盖'))
    sheet(book,'重复出现31组62行',['原文件','工作表','原行号','历史单号','历史单据时间','原追溯码','历史记录数','本次匹配视图记录数','处理建议'],repeated)
    quality=json.loads((HERE/'quality-real-samples.json').read_text('utf8'))['samples']
    sheet(book,'现有质量函数实测',['样本类别','整单码行数','质量状态','阻断规则'],[(x['category'],x.get('whole_code_rows'),x.get('quality_state'),','.join(x.get('blocking_rule_codes',[]))) for x in quality])
    sheet(book,'建议修改与责任',['事项','处理方式','责任/验收'],[
        ('5077条另一单号分支','先用本轮sql目录两条SELECT核实原表与三键JOIN；若目标含该业务再新建来源revision','DBA/HIS/药学部，不直接改生产JOIN或让无主单记录发送'),
        ('1396条UNKNOWN','保留拦截，分别处理实际退款/退药事件；勿映射成正常321','HIS/药学部确认退款与实物退药口径'),
        ('31组同单同码','查SPLIT_FLAG及更正来源；确定可审计处理规则','药学部/HIS/开发，不放松QD-DUP-CODE'),
        ('同时间全量双向对账','审核来源需提供CREATE_TIME及IN_OUT_TYPE/删除/拆零原值','DBA/开发新增审计契约，不改历史冻结包'),
        ('齐套和晚到变化','保留原先A01—A05任务，新revision落实稳定检查/回看','开发，非本轮DDL执行')])
    db.close();book.save(output)
    check=load_workbook(output,read_only=True,data_only=True)
    counts={ws.title:ws.max_row-1 for ws in check};check.close()
    assert counts['视图未匹配5077']==5077 and counts['当前UNKNOWN1396']==1396 and counts['重复出现31组62行']==62
    report={'workbook':str(output),'sheets':counts,'old_workbook_overwritten':False,'raw_data_in_shared_report':False,'uploads':0}
    (HERE/'private-workbook-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
