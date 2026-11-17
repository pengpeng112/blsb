"""Offline delivery checks; print counts only, never identifiers or credentials."""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess

from openpyxl import load_workbook

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    meta = json.loads((HERE / 'private-workbook-validation.json').read_text('utf8'))
    book = load_workbook(meta['workbook'], read_only=True, data_only=True)
    expected = {'视图未匹配5077': 5077, '当前UNKNOWN1396': 1396, '重复出现31组62行': 62}
    rows = 0
    for name, count in expected.items():
        seen = 0
        for row in book[name].iter_rows(min_row=2):
            bill, code = row[3], row[5]
            assert isinstance(bill.value, str) and bill.data_type == 's'
            assert isinstance(code.value, str) and code.data_type == 's'
            assert re.fullmatch(r'[0-9]{20}', code.value)
            assert bill.number_format == '@' and code.number_format == '@'
            assert isinstance(row[2].value, int) and row[2].value >= 2
            seen += 1
        assert seen == count
        rows += seen
    book.close()
    prior = json.loads((ROOT / 'docs/development/drug-trace-history-compare-20261023/history-summary.json').read_text('utf8'))
    for item in prior['files']:
        source = ROOT / '数据上报/1、码上放心平台/历史上报数据' / item['file']
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item['sha256']
    scripts = list(HERE.glob('*.py'))
    for path in scripts:
        ast.parse(path.read_text('utf8'), filename=str(path))
    path = Path('F:/python/数据资产/.agents/skills/hisuser-readonly-sql/scripts/validate_his_sql.py')
    spec = importlib.util.spec_from_file_location('his_static_validator', path)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    sql_results = {}
    for sql in (HERE / 'sql').glob('*.sql'):
        errors = validator.validate(sql.read_text('utf8'))
        assert not errors, sql.name
        sql_results[sql.name] = {'static_passed': True, 'executed': False}
    full = json.loads((HERE / 'evidence/full-compare-r1.json').read_text('utf8'))
    diag = json.loads((HERE / 'evidence/diagnose-r1.json').read_text('utf8'))
    probe = json.loads((ROOT / 'docs/development/drug-trace-history-transfer-audit-20261023/evidence/reader-r1.json').read_text('utf8'))
    protected = [Path(meta['workbook']), Path(meta['workbook']).parent,
                 Path(full['private_details_path']), Path(diag['private_details_path'])]
    for target in protected:
        quoted = str(target).replace("'", "''")
        ps = "$ErrorActionPreference='Stop';$allowed=@(([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value,'S-1-5-18');"
        ps += "$a=Get-Acl -LiteralPath '" + quoted + "';"
        ps += "$bad=@($a.Access | Where-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowed });"
        ps += "if($bad.Count -ne 0){exit 2};if($a.Access.Count -lt 2){exit 3};exit 0"
        result = subprocess.run([shutil.which('pwsh') or 'powershell', '-NoProfile', '-Command', ps], capture_output=True)
        assert result.returncode == 0, 'PRIVATE_ACL_CHECK_FAILED'
    queries = len(probe['queries']) + len(full['queries']) + len(full['batches']) + len(diag['queries']) + len(diag['diagnostic_batches'])
    assert queries == 972
    report = {'passed': True, 'private_identifier_rows_verified': rows,
              'identifiers_text_and_20_digit_codes': True, 'private_acl_user_system_only': True,
              'original_xlsx_hashes_unchanged': len(prior['files']), 'python_scripts_ast_passed': len(scripts),
              'dba_sql': sql_results, 'actual_readonly_selects': queries,
              'all_historical_key_batches_completed': full['stop_reason'] == 'ALL_REQUESTED_CODES_QUERIED',
              'same_create_time_bidirectional_audit': False,
              'source_writes': 0, 'uploads': 0, 'production_changes': 0}
    (HERE / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
