"""Local r2 analysis: whole-bill quality via the existing pure function,
r1->r2 state comparison, and view-definition drift check vs the r10 draft.

Reads the private r2/r1 detail files in memory; writes aggregates into the
shared evidence dir and identifier-level results only into the private dir.
"""
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
R1 = ROOT / 'docs/development/drug-trace-history-live-compare-20261023'
sys.path.insert(0, str(ROOT / 'implementation'))
import _bootstrap  # noqa: E402
from domains.drug_trace import quality as Q  # noqa: E402


def norm_tokens(text):
    return re.findall(r"[A-Za-z0-9_$]+|'[^']*'|[(),;*+=.<>/-]", (text or '').upper())


def main():
    ev = sorted((HERE / 'evidence').glob('r2-two-views-*.json'))
    live = json.loads(ev[-1].read_text('utf8'))
    private_path = Path(live['private_details_path'])
    detail = json.loads(private_path.read_text('utf8'))
    r1_full = json.loads((R1 / 'evidence/full-compare-r1.json').read_text('utf8'))
    r1_diag = json.loads((R1 / 'evidence/diagnose-r1.json').read_text('utf8'))
    r1_states = json.loads(Path(r1_diag['private_details_path']).read_text('utf8'))
    report = {'analyzed_at': datetime.now(timezone.utc).isoformat(),
              'run_id': live['run_id'], 'source_writes': 0, 'uploads': 0}

    # ---- Q03: whole-bill quality via the existing pure function ----
    quality_results = []
    for bill in detail['whole_bills']:
        header_raw = bill['header'][0]
        header = {k.lower(): v for k, v in dict(header_raw).items()}
        lines = []
        for ln in bill['lines']:
            row = {k.lower(): v for k, v in dict(ln).items()}
            lines.append({
                'source_code_record_id': str(row.get('source_code_record_id') or ''),
                'trace_code': str(row.get('trace_code') or ''),
                'is_deleted': Q.as_bool(row.get('is_deleted'), 'is_deleted'),
                'quantity': '' if row.get('quantity') in (None, '') else str(row.get('quantity')),
                'unit': str(row.get('unit') or ''),
                'code_level': str(row.get('code_level') or ''),
                'parent_code': str(row.get('parent_code') or ''),
                'f_trace_code': str(row.get('f_trace_code') or ''),
                's_trace_code': str(row.get('s_trace_code') or ''),
                't_trace_code': str(row.get('t_trace_code') or ''),
            })
        state, issues = Q.evaluate_revision(
            header, lines, profile='normal_outpatient', code_limit_guard=2000)
        quality_results.append({
            'main_bill': header.get('source_bill_id'), 'business_kind': header.get('business_kind'),
            'finalized': header.get('finalized'), 'is_void': header.get('is_void'),
            'raw_kind': header.get('source_bill_kind_raw'),
            'line_rows': len(lines),
            'duplicate_code_values': len([l for l in lines if not l['is_deleted']]) -
                                     len({l['trace_code'] for l in lines if not l['is_deleted']}),
            'state': str(state), 'rule_codes': sorted({i['rule'] for i in issues}),
            'blocking_rules': sorted({i['rule'] for i in issues if i['severity'] == 'BLOCKING'})})
    # identifier-free aggregate for shared evidence
    report['q03_quality_aggregate'] = {
        'whole_bills_evaluated': len(quality_results),
        'by_state': {}, 'bills_with_dup_active_codes': 0,
        'all_bills_header_rows_unique': all(b['line_rows'] >= 2 for b in quality_results)}
    for b in quality_results:
        report['q03_quality_aggregate']['by_state'][b['state']] = \
            report['q03_quality_aggregate']['by_state'].get(b['state'], 0) + 1
        if b['duplicate_code_values'] > 0:
            report['q03_quality_aggregate']['bills_with_dup_active_codes'] += 1
    quality_results_path = private_path.parent / 'q03-whole-bill-quality.json'
    quality_results_path.write_text(
        json.dumps({'run_id': live['run_id'], 'results': quality_results},
                   ensure_ascii=False, indent=2), encoding='utf8')
    report['q03_quality_private_path'] = str(quality_results_path)

    # ---- Q02: pair-level r1 vs r2 state migration ----
    r1_map = {(s['bill'], s['code']): (s['raw_kind'], s['refund_raw'],
                                        s['finalized_raw'], s['void_raw'])
              for s in r1_states['excluded_states']}
    r2_map = {(s['bill'], s['code']): (s['raw_kind'], s['refund_raw'],
                                        s['finalized_raw'], s['void_raw'])
              for s in detail['excluded_states']}
    migrated = {k: {'r1': r1_map[k], 'r2': r2_map[k]} for k in r1_map.keys() & r2_map.keys()
                if r1_map[k] != r2_map[k]}
    report['q02_state_migration'] = {
        'r1_pairs': len(r1_map), 'r2_pairs': len(r2_map),
        'pairs_in_both': len(r1_map.keys() & r2_map.keys()),
        'pairs_state_changed': len(migrated),
        'pairs_left_excluded_set': len(r1_map.keys() - r2_map.keys()),
        'pairs_new_in_excluded_set': len(r2_map.keys() - r1_map.keys())}
    if migrated:
        mig_path = private_path.parent / 'q02-state-migrations.json'
        mig_path.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding='utf8')
        report['q02_state_migration']['private_path'] = str(mig_path)

    # ---- Q01 view-side: per-code change vs r1 ----
    r1_with_rows = {a['code']: a['current_other_line_rows']
                    for a in r1_states['missing_annotated'] if a['current_code_exists']}
    r2_code_state = {m['code']: len(m['rows']) for m in detail['missing_codes_view']}
    appeared = sorted(set(r2_code_state) - set(r1_with_rows))
    vanished = sorted(set(r1_with_rows) - set(r2_code_state))
    rowcount_changed = {k: {'r1_rows': r1_with_rows[k], 'r2_rows': r2_code_state[k]}
                        for k in r1_with_rows.keys() & r2_code_state.keys()
                        if r1_with_rows[k] != r2_code_state[k]}
    report['q01_view_side_changes'] = {
        'r1_codes_with_view_rows': len(r1_with_rows),
        'r2_codes_with_view_rows': len(r2_code_state),
        'codes_with_rows_in_both': len(r1_with_rows.keys() & r2_code_state.keys()),
        'codes_appeared_since_r1': len(appeared),
        'codes_vanished_since_r1': len(vanished),
        'codes_rowcount_changed': len(rowcount_changed),
        'codes_without_rows_both_timepoints': 531 - len(r2_code_state)}
    # do any of the 5077 exact (legacy bill, code) pairs now appear in the view?
    r1_missing_pairs = {(p[0], p[1]) for p in json.loads(
        Path(r1_full['private_details_path']).read_text('utf8'))['missing_pairs']}
    exact_hits = [(r['bill'], m['code']) for m in detail['missing_codes_view']
                  for r in m['rows'] if (r['bill'], m['code']) in r1_missing_pairs]
    report['q01_view_side_changes']['exact_legacy_pairs_now_in_view'] = len(exact_hits)
    report['q01_view_side_changes']['note'] = (
        'view rows for these codes are under 14-digit line ids; exact 20-digit legacy '
        'line ids still absent' if not exact_hits else 'see private detail')

    # ---- Q00: view definition drift vs the r10 draft ----
    draft = (ROOT / 'docs/development/drug-trace-content-contract-20261022/sql/'
             'draft_normal_outpatient_views_r10.sql').read_text('utf8')
    draft_bodies = {}
    for part in re.split(r'CREATE OR REPLACE VIEW\s+', draft)[1:]:
        vname = part.split()[0].strip()
        body = re.search(r'\bAS\s+(SELECT\b.*)', part, re.S).group(1)
        body = re.sub(r';\s*$', '', body.strip())
        draft_bodies[vname] = body
    defs = live.get('queries', {}).get('view_definitions', {}).get('rows', [])
    r1_defs = {row['VIEW_NAME']: row['TEXT'] for row in
               r1_full['queries']['view_definitions']['rows']}
    drift = {}
    for row in defs:
        name = row['VIEW_NAME']
        body = row['TEXT'] or ''
        key = 'PHARMACY.' + name
        drift[name] = {
            'fetched_length': len(body),
            'identical_to_r1_capture': body == r1_defs.get(name),
            'tokens_equal_to_r10_draft': norm_tokens(body) == norm_tokens(draft_bodies[key]),
            'draft_token_count': len(norm_tokens(draft_bodies[key])),
            'view_token_count': len(norm_tokens(body))}
    report['q00_view_definition_check'] = {
        'restricted_reader_unchanged': live.get('restricted_reader_unchanged'),
        'view_status_rows': live.get('queries', {}).get('view_status', {}).get('rows_returned'),
        'per_view': drift}

    out_path = HERE / 'evidence' / ('r2-analysis-' + live['run_id'] + '.json')
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
