"""Build final classification evidence + r2b restricted workbook from the
source-audit results. Creates NEW files only; r1/r2 artifacts untouched.
"""
from pathlib import Path
from datetime import datetime
import json
import os
import sqlite3
from collections import Counter
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sheet(book, name, headers, rows):
    ws = book.create_sheet(name)
    ws.append(headers)
    for row in rows:
        ws.append(list(row))
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.number_format = '@'
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='24476A')
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 28
    return ws


def main():
    audit = json.loads(sorted((HERE / 'evidence').glob('source-audit-20261023T14*.json'))[-1].read_text('utf8'))
    dailyfix = json.loads(sorted((HERE / 'evidence').glob('source-audit-dailyfix-*.json'))[-1].read_text('utf8'))
    stage_e = json.loads(sorted((HERE / 'evidence').glob('source-audit-stageE-*.json'))[-1].read_text('utf8'))
    priv = Path(audit['private_dir'])
    rows = json.loads((priv / 'source-rows.json').read_text('utf8'))
    cls = json.loads((priv / 'pair-classification.json').read_text('utf8'))
    dup = json.loads((priv / 'dup-analysis.json').read_text('utf8'))

    # ---- final classification summary ----
    exact_recs = {}
    for r in rows['sql04_missing']:
        exact_recs[(str(r['PRESC_DETAILID']), str(r['T_TRACE_CODE']))] = r
    final = {
        'run_id': audit['run_id'],
        'identity': audit['identity'],
        'root_cause_5077': {
            'u_class': 'U02 (all 5077 rows, conservation OK)',
            'finding': 'every (code, legacy PRESC_DETAILID) record exists in DRUG_USER.MED_TRACE_RECORD',
            'record_attributes': {'IN_OUT_TYPE': 'OUT (5077/5077)', 'IS_DELETED': '0 (5077/5077)',
                                  'SPLIT_FLAG': '1 (5077/5077) - split dispensing flag',
                                  'STORE_CODE': "single value '050121' (5077/5077)",
                                  'TRACE_BILLNO': '20-digit numeric, 2963 distinct',
                                  'CREATE_TIME': 'spread 2026-07..2026-10 matching history export'},
            'master_side_discriminators': {
                'e1_prescno_lengths_in_window': stage_e['e1_prescno_length']['groups'],
                'e1_conclusion': 'master PRESC_NO in window is 1-5 digits only; 20-digit numbers do not exist',
                'e2_dispensary': stage_e['e2_dispensary']['groups'],
                'e2_conclusion': "pharmacy 050121 IS the largest master pharmacy (102644 rows) - not a store mismatch",
                'e3': 'S01-S03 master by (PRESC_NO, PRESC_DATE) without store: 0 rows - no master with that number at all',
            },
            'conclusion': ('5077 rows are split-dispensing (SPLIT_FLAG=1) OUT scans of pharmacy 050121 '
                           'whose 20-digit TRACE_BILLNO is outside the outpatient prescription number '
                           'space; the r10 3-key INNER JOIN cannot match them by design. They are NOT '
                           'missing uploads of normal prescriptions and NOT outpatient under-reporting; '
                           'covering them requires a separate source revision after HIS/pharmacy confirms '
                           'the split-dispensing business stream and its master table / bill semantics.'),
        },
        'dup_31_cause': {
            'pairs': 31, 'distinct_main_bills': 11, 'whole_bill_quality': 'INVALID x11 (QD-DUP-CODE)',
            'per_pair_source_records': 'exactly 2, both OUT, IS_DELETED=0, SPLIT_FLAG=0, master JOIN hit',
            'create_time_span': 'all 31 pairs < 1 hour (double scan), buckets lt1h=31',
            'conclusion': ('genuine double-scan (rescan within the hour), not split dispensing and not '
                           'view duplication; keep QD-DUP-CODE block; correction/rescan rule needs '
                           'pharmacy/HIS business decision (L04/C04) before any audited handling'),
            'side_note': ('the same 31 codes also appear once each under other pharmacies (050106/050105) '
                          'as normal SPLIT_FLAG=0 records - separate events, not part of the dup pairs'),
        },
        'daily_coverage_master_driven': {
            'days': 81, 'totals': {'masters': 245794, 'masters_with_out': 142080,
                                   'masters_no_out': 103714, 'out_rows_attached': 484567},
            'cross_check': ('484567 OUT-attached rows by PRESC_DATE window vs r1 view 484539 rows within '
                            'history dates by bill_time - consistent; view is not dropping joined rows'),
            'inpatient': 'PRESC_SOURCE=1 masters (90024) have zero OUT scan records in window',
            'limitation': ('T-driven global JOIN_MISS count NOT run: MED_TRACE_RECORD has no PRESC_DATE/'
                           'CREATE_TIME index (SQL06 confirmed); the 5077 U02 pairs are the only '
                           'confirmed JOIN-miss population via exact-key evidence'),
        },
        'conservation': {'u02_rows': 5077, 'sum_check': 'U02=5077 OK (U01/U03/U04/U05=0)'},
        'side_effects': {'source_writes': 0, 'uploads': 0, 'production_changes': 0},
    }
    (HERE / 'evidence' / 'classification-final.json').write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding='utf8')

    # ---- r2b workbook ----
    output = priv / '历史上报根因核查_源端归因_r2b.xlsx'
    if output.exists():
        raise RuntimeError('EXISTING_WORKBOOK_MUST_BE_PRESERVED')
    db = sqlite3.connect((Path(os.environ['LOCALAPPDATA']) /
                          'BLYB/history-compare-20261023/history.sqlite3').as_uri() + '?mode=ro', uri=True)
    book = Workbook()
    book.remove(book.active)
    sheet(book, '核查说明', ['项目', '结果/边界'], [
        ('任务/时点', 'DTR-HISTORY-SOURCE-AUDIT-20261023，run ' + audit['run_id'] +
         '；用户明确授权后经<relay-jump>受控连接器以平台只读账号执行源表SELECT'),
        ('5077条根因', '全部U02：原(码,旧明细号)记录都在源表；SPLIT_FLAG=1、药房050121、'
         'TRACE_BILLNO为20位数字；主单表该窗口PRESC_NO仅1-5位，20位号不存在（E1），'
         '050121是主单表最大药房（E2），S01-S03按号+日期不带药房查主单0行（E3）'),
        ('结论', '拆零(拆分)扫码业务流的单号不在门诊处方主单编号空间，r10三键JOIN按设计不可命中；'
         '非正常门诊漏报；是否覆盖由院方确认业务后另立来源revision'),
        ('31组重复', '每组恰好2条源记录，均OUT/未删/SPLIT_FLAG=0/JOIN命中，创建间隔<1小时：真实双扫；'
         '11张整单全部INVALID/QD-DUP-CODE保持阻断；处理规则待药学/HIS定'),
        ('逐日覆盖', '81天：主单245794张，142080张有OUT码（484567行），与r1视图窗口行数一致；'
         '住院主单90024张窗口内零OUT码'),
        ('反向限制', '源表无PRESC_DATE/CREATE_TIME索引(SQL06证实)，未做T侧全量JOIN_MISS计数；'
         '5077条U02是精确键证据下的确认JOIN缺失人群'),
        ('副作用', '源写0、上传0、生产改动0；既有1单4码成功不撤销不重发'),
    ])
    missing_rows = []
    for item in cls:
        rec = exact_recs.get((item['bill'], item['code']))
        for f, sh, rn, b, bt, c in db.execute(
                'SELECT file,sheet,row_no,bill,bill_time,code FROM history WHERE bill=? AND code=? '
                'ORDER BY file,row_no', (item['bill'], item['code'])):
            missing_rows.append((f, sh, rn, b, bt, c, item['u'],
                                 'SPLIT_FLAG=%s;店=%s;单号20位;OUT;未删;三键JOIN未命中' % (
                                     rec.get('SPLIT_FLAG') if rec else '?',
                                     rec.get('STORE_CODE') if rec else '?'),
                                 '拆零流记录；院方确认业务范围后另立来源revision，不并入正常门诊'))
    sheet(book, '未匹配5077源端归因', ['原文件', '工作表', '原行号', '历史单号', '历史单据时间', '原追溯码',
                                     '主分类', '源记录特征', '处理建议'], missing_rows)
    dup_rows = []
    for d in dup:
        for f, sh, rn, b, bt, c in db.execute(
                'SELECT file,sheet,row_no,bill,bill_time,code FROM history WHERE bill=? AND code=? '
                'ORDER BY file,row_no', (d['bill'], d['code'])):
            span = d['create_span_seconds']
            dup_rows.append((f, sh, rn, b, bt, c, d['exact_records'],
                             'OUT/未删/SPLIT_FLAG=0/JOIN命中',
                             ('%.0f秒' % span) if span is not None else '?',
                             '真实双扫(<1小时)；整单INVALID保持；处理规则待药学/HIS；不用DISTINCT'))
    sheet(book, '重复31组源端成因', ['原文件', '工作表', '原行号', '历史单号', '历史单据时间', '原追溯码',
                                   '源记录数', '记录特征', '两次扫码间隔', '处理建议'], dup_rows)
    daily_rows = []
    for day in dailyfix['daily_coverage']:
        if not day.get('ok'):
            daily_rows.append((day['day'], '查询失败', '', '', '', ''))
            continue
        for g in day['groups']:
            daily_rows.append((g['PRESC_DAY'], 'PRESC_SOURCE=%s' % g['PRESC_SOURCE'],
                               g['MASTERS'], g['MASTERS_WITH_OUT'], g['MASTERS_NO_OUT_ROWS'],
                               g['OUT_ROWS']))
    sheet(book, '逐日覆盖81天', ['处方日期', '来源', '主单数', '有OUT码主单', '无OUT码主单', 'OUT码行数'], daily_rows)
    db.close()
    book.save(output)
    check = load_workbook(output, read_only=True, data_only=True)
    counts = {ws.title: ws.max_row - 1 for ws in check}
    check.close()
    assert counts['未匹配5077源端归因'] == 5077 and counts['重复31组源端成因'] == 62, counts
    report = {'workbook': str(output), 'sheets': counts,
              'r2_workbook_overwritten': False, 'uploads': 0,
              'built_at': datetime.now().isoformat()}
    (HERE / 'workbook-r2b-validation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
