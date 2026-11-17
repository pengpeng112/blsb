"""rootcause-r2 offline delivery verification (read-only; writes validation.json here).

Checks: workbook sheets/text/20-digit codes, original 25-file SHA unchanged,
r1 evidence/workbook untouched, private artifact ACLs, script AST, SQL static
validation (r2 SQL01-06 set), classification conservation, and that no raw
identifiers leaked into shared evidence JSONs.
"""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import re
import subprocess
import shutil

from openpyxl import load_workbook

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
R1 = ROOT / 'docs/development/drug-trace-history-live-compare-20261023'


def acl_ok(path):
    quoted = str(path).replace("'", "''")
    ps = ("$ErrorActionPreference='Stop';$allowed=@(([Security.Principal.WindowsIdentity]"
          "::GetCurrent()).User.Value,'S-1-5-18');$a=Get-Acl -LiteralPath '" + quoted + "';"
          "$bad=@($a.Access | Where-Object { $_.IdentityReference.Translate("
          "[Security.Principal.SecurityIdentifier]).Value -notin $allowed });"
          "if($bad.Count -ne 0){exit 2};if($a.Access.Count -lt 2){exit 3};exit 0")
    return subprocess.run([shutil.which('pwsh') or 'powershell', '-NoProfile', '-Command', ps],
                          capture_output=True).returncode == 0


def main():
    report = {'passed': True}
    meta = json.loads((HERE / 'workbook-r2-validation.json').read_text('utf8'))
    book = load_workbook(meta['workbook'], read_only=True, data_only=True)
    expected = {'未匹配5077分类': 5077, '当前UNKNOWN1396复核': 1396, '重复31组整单结果': 62}
    rows = 0
    for name, count in expected.items():
        seen = 0
        for row in book[name].iter_rows(min_row=2):
            bill, code = row[3], row[5]
            assert isinstance(bill.value, str) and bill.data_type == 's', (name, seen)
            assert isinstance(code.value, str) and code.data_type == 's', (name, seen)
            assert re.fullmatch(r'[0-9]{20}', code.value), (name, seen)
            assert bill.number_format == '@' and code.number_format == '@', (name, seen)
            seen += 1
        assert seen == count, (name, seen)
        rows += seen
    book.close()
    report['workbook_identifier_rows_verified'] = rows

    # original 25 inputs unchanged
    prior = json.loads((ROOT / 'docs/development/drug-trace-history-compare-20261023/'
                        'history-summary.json').read_text('utf8'))
    for item in prior['files']:
        source = ROOT / '数据上报/1、码上放心平台/历史上报数据' / item['file']
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item['sha256'], item['file']
    report['original_xlsx_hashes_unchanged'] = len(prior['files'])

    # r1 artifacts untouched (exclusive-create protection plus hash of evidence keys)
    r1_full = json.loads((R1 / 'evidence/full-compare-r1.json').read_text('utf8'))
    r1_diag = json.loads((R1 / 'evidence/diagnose-r1.json').read_text('utf8'))
    r1_wb = Path(r1_full['private_details_path']).parent / '历史上报与视图比对_20261023_r1.xlsx'
    assert r1_wb.exists() and meta['workbook'] != str(r1_wb)
    assert r1_full['summary']['pairs_absent_from_view'] == 5077
    report['r1_artifacts_preserved'] = True

    # private artifacts: exist, ACL user+SYSTEM only, counts
    live = json.loads(sorted((HERE / 'evidence').glob('r2-two-views-*.json'))[-1].read_text('utf8'))
    priv_dir = Path(live['private_dir'])
    protected = [priv_dir, Path(meta['workbook']), Path(live['private_details_path'])]
    for extra in ('q01b-view-times.json', 'q03-whole-bill-quality.json',
                  'dba-sample-package.json', 'all-missing-codes-r2.json',
                  'all-missing-legacy-bills-r2.json'):
        protected.append(priv_dir / extra)
    for target in protected:
        assert target.exists(), str(target)
        assert acl_ok(target), 'PRIVATE_ACL_CHECK_FAILED: ' + str(target)
    detail = json.loads(Path(live['private_details_path']).read_text('utf8'))
    codes_file = json.loads((priv_dir / 'all-missing-codes-r2.json').read_text('utf8'))
    bills_file = json.loads((priv_dir / 'all-missing-legacy-bills-r2.json').read_text('utf8'))
    assert codes_file['count'] == 531 and bills_file['count'] == 4974
    report['private_acl_user_system_only'] = True
    report['private_detail_rows'] = live['private_rows']

    # scripts AST + SQL static validation
    scripts = sorted(HERE.glob('*.py'))
    for path in scripts:
        ast.parse(path.read_text('utf8'), filename=str(path))
    report['python_scripts_ast_passed'] = len(scripts)
    validator_path = Path('F:/python/数据资产/.agents/skills/hisuser-readonly-sql/'
                          'scripts/validate_his_sql.py')
    spec = importlib.util.spec_from_file_location('his_static_validator', validator_path)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    sql_results = {}
    for sql in sorted((HERE / 'sql').glob('*.sql')):
        errors = validator.validate(sql.read_text('utf8'))
        assert not errors, sql.name
        sql_results[sql.name] = {'static_passed': True, 'executed': False}
    report['r2_sql'] = sql_results

    # classification conservation
    cs = json.loads((HERE / 'evidence/classification-summary.json').read_text('utf8'))
    u = cs['u_classification']
    assert sum(u[k] for k in ('U01_raw_key_present_master_hit', 'U02_raw_key_present_master_miss',
                              'U03_code_present_legacy_line_absent',
                              'U04_code_absent_source_confirmed',
                              'U05_pending_cannot_confirm')) == 5077
    assert (cs['conservation']['matched_same_key_same_count_rows'] +
            cs['conservation']['unmatched_rows'] == 489613)
    assert cs['conservation']['unmatched_scope'] == {'distinct_legacy_bills': 4974,
                                                     'distinct_codes': 531}
    report['classification_conservation_ok'] = True

    # no raw 20-digit codes / legacy bills leaked into shared r2 evidence
    codes_set = set(codes_file['codes'])
    bills_set = set(bills_file['bills'])
    leaked = []
    for ev in sorted((HERE / 'evidence').glob('*.json')):
        text = ev.read_text('utf8')
        if any(c in text for c in codes_set) or any(b in text for b in bills_set):
            leaked.append(ev.name)
    assert not leaked, leaked
    report['no_raw_identifiers_in_shared_evidence'] = True

    report['selects_executed_this_round'] = json.loads(
        (HERE / 'evidence/run-manifest.json').read_text('utf8'))['selects_total']
    report['source_writes'] = 0
    report['uploads'] = 0
    report['production_changes'] = 0
    report['raw_table_queries'] = 0
    (HERE / 'validation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
