"""Build the r2 restricted workbook (new file; no r1 artifact touched).

Sheets: summary / 5077 per-pair U-classification with view-side sublabels /
1396 current states / 31 dup groups with whole-bill quality results.
All identifier columns forced to text format ('@').
"""
from pathlib import Path
from datetime import datetime
import json
import os
import sqlite3
from collections import Counter
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
R1 = ROOT / 'docs/development/drug-trace-history-live-compare-20261023'


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
    live = json.loads(sorted((HERE / 'evidence').glob('r2-two-views-*.json'))[-1].read_text('utf8'))
    private = Path(live['private_dir'])
    detail = json.loads((private / 'r2-details.json').read_text('utf8'))
    q01b_rows = json.loads((private / 'q01b-view-times.json').read_text('utf8'))['view_rows']
    quality = json.loads((private / 'q03-whole-bill-quality.json').read_text('utf8'))['results']
    r1_full = json.loads((R1 / 'evidence/full-compare-r1.json').read_text('utf8'))
    missing_pairs = json.loads(Path(r1_full['private_details_path']).read_text('utf8'))['missing_pairs']
    output = private / '历史上报根因核查_20261023_r2.xlsx'
    if output.exists():
        raise RuntimeError('EXISTING_WORKBOOK_MUST_BE_PRESERVED')

    db = sqlite3.connect((Path(os.environ['LOCALAPPDATA']) /
                          'BLYB/history-compare-20261023/history.sqlite3').as_uri() + '?mode=ro', uri=True)
    loc = lambda bill, code: db.execute(
        'SELECT file,sheet,row_no,bill,bill_time,code FROM history WHERE bill=? AND code=? '
        'ORDER BY file,row_no', (bill, code)).fetchall()

    from datetime import datetime as _dt
    rows_by_code = {}
    for r in q01b_rows:
        rows_by_code.setdefault(r['code'], []).append(r)

    def sublabel(bill, code):
        vr = rows_by_code.get(code)
        if not vr:
            return '两视图未见该码', ''
        row = db.execute('SELECT bill_time FROM history WHERE bill=? AND code=? ORDER BY row_no LIMIT 1',
                         (bill, code)).fetchone()
        if not row:
            return '同码在其他明细可见', ''
        ht = _dt.fromisoformat(row[0])
        best = None
        for r in vr:
            try:
                vt = _dt.fromisoformat(r['bt'][:19])
            except ValueError:
                continue
            d = (vt.date() - ht.date()).days
            if best is None or abs(d) < abs(best):
                best = d
        if best is None:
            return '同码在其他明细可见', ''
        return ('同码同日在其他明细可见' if best == 0 else '同码异日在其他明细可见'), best

    book = Workbook()
    book.remove(book.active)
    counts = Counter()
    for bill, code, n, *_ in missing_pairs:
        counts[sublabel(bill, code)[0]] += 1
    rows = [
        ('任务/时点', 'DTR-HISTORY-ROOTCAUSE-R2，run ' + live['run_id'] +
         '（r1 基线 2026-10-23T04:10Z 后约 6.6 小时复核）'),
        ('身份/授权', 'BLYB_DRUGTRACE 仅两视图 SELECT（本轮再实测不变）；原表 SQL 未执行'),
        ('5077 未匹配', '全部 U05 待核（源表未查）；' +
         '；'.join(f'{k} {v} 行' for k, v in sorted(counts.items()))),
        ('同码同日说明', '同日=历史导出日期与该码当前视图行发药日相同；提示同事件异标识可能，不构成证明'),
        ('r1→r2 视图侧变化', '0 码出现/消失/行数变化；0 条精确 20 位旧键进入视图'),
        ('1396 UNKNOWN', '三态 46/23/1327 与 r1 完全一致，411 主单，零状态迁移；保留正常发送拦截'),
        ('31 组重复', '11 张不同主单；整单取全（无截断）；全部 INVALID/QD-DUP-CODE；'
         '每张单全部有效码均恰出现 2 次（整单双扫模式）；成因待源表 SPLIT_FLAG 证据'),
        ('同 CREATE_TIME 反向', '未完成：视图无 CREATE_TIME、源表无该索引（快照）；SQL05/06 已备'),
        ('副作用', '源写 0、上传 0、平台调用 0、生产改动 0'),
        ('边界', '未匹配不等于漏报；98.9631% 匹配不等于可全量发送；既有 1 单 4 码成功不撤销不重发'),
    ]
    sheet(book, '核查说明', ['项目', '结果/边界'], rows)

    missing_rows = []
    for bill, code, n, *_ in missing_pairs:
        label, delta = sublabel(bill, code)
        advice = 'DBA 按 SQL01—04 查原记录与三键 JOIN 后归入 U01—U04；不直接补报'
        for f, sh, rn, b, bt, c in loc(bill, code):
            missing_rows.append((f, sh, rn, b, bt, c, 'U05 待核', label, delta, advice))
    sheet(book, '未匹配5077分类', ['原文件', '工作表', '原行号', '历史单号', '历史单据时间', '原追溯码',
                                  '主分类', '视图侧子标签', '最近发药日差(天)', '处理建议'], missing_rows)

    excluded_rows = []
    for s in detail['excluded_states']:
        for f, sh, rn, b, bt, c in loc(s['bill'], s['code']):
            excluded_rows.append((f, sh, rn, b, bt, c, s['raw_kind'], s['refund_raw'],
                                  s['finalized_raw'], s['void_raw'],
                                  '与r1一致无迁移；全退/退费不属正常门诊分支，不改报321'))
    sheet(book, '当前UNKNOWN1396复核', ['原文件', '工作表', '原行号', '历史单号', '历史单据时间', '原追溯码',
                                       '主单原始分类状态', '退费原始值', 'finalized原值', 'is_void原值', '处理建议'],
          excluded_rows)

    # dup groups: locate history rows; find view rows + quality state of the owning bill
    dup_view = {}
    for rec in detail['dup_pair_view_rows']:
        dup_view.setdefault((rec['bill'], rec['code']), []).append(rec)
    bill_quality = {}
    for wb_ in detail['whole_bills']:
        h = {k.lower(): v for k, v in dict(wb_['header'][0]).items()}
        active = [str(l.get('TRACE_CODE') or '') for l in wb_['lines'] if str(l.get('IS_DELETED')) != '1']
        cc = Counter(active)
        q = next((x for x in quality if x['main_bill'] == h.get('source_bill_id')), None)
        bill_quality[h['source_bill_id']] = {
            'state': q['state'] if q else '?', 'rules': ','.join(q['blocking_rules']) if q else '?',
            'dup_groups': sum(1 for v in cc.values() if v > 1), 'lines': len(wb_['lines'])}
    dup_rows = []
    for (bill, code), recs in sorted(dup_view.items()):
        mains = {r['main'] for r in recs}
        qinfo = '; '.join(f"{bill_quality[m]['state']}/{bill_quality[m]['rules']}"
                          for m in mains if m in bill_quality)
        for f, sh, rn, b, bt, c in loc(bill, code):
            dup_rows.append((f, sh, rn, b, bt, c, len(recs),
                             ';'.join(sorted(mains)), qinfo,
                             '整单INVALID保持阻断；待SQL04查SPLIT_FLAG/重扫成因，不用DISTINCT掩盖'))
    sheet(book, '重复31组整单结果', ['原文件', '工作表', '原行号', '历史单号', '历史单据时间', '原追溯码',
                                   '视图记录数', '所属主单(source_bill_id)', '整单质量/阻断规则', '处理建议'],
          dup_rows)
    db.close()
    book.save(output)

    check = load_workbook(output, read_only=True, data_only=True)
    counts = {ws.title: ws.max_row - 1 for ws in check}
    check.close()
    assert counts['未匹配5077分类'] == 5077, counts
    assert counts['当前UNKNOWN1396复核'] == 1396, counts
    assert counts['重复31组整单结果'] == 62, counts
    report = {'workbook': str(output), 'sheets': counts, 'r1_workbook_overwritten': False,
              'raw_data_in_shared_report': False, 'uploads': 0,
              'built_at': datetime.now().isoformat()}
    (HERE / 'workbook-r2-validation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
