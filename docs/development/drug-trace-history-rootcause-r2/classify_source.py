"""Classify the 5077 unmatched pairs from source-audit results (U01-U05),
analyze the 31 dup-group causes, and aggregate daily coverage.

Raw identifiers stay in the private dir; only aggregates reach shared evidence.
"""
from pathlib import Path
from datetime import datetime, timezone, date, timedelta
import json
import re
from collections import Counter

HERE = Path(__file__).resolve().parent


def as_dt(v):
    if v is None:
        return None
    s = str(v).replace('T', ' ')[:19]
    try:
        return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def main():
    audit = json.loads(sorted((HERE / 'evidence').glob('source-audit-2*.json'))[-1]
                       .read_text('utf8'))
    assert audit.get('run_completed'), 'audit run incomplete'
    rows = json.loads(Path(audit['private_rows_path']).read_text('utf8'))
    prev = json.loads(sorted((HERE / 'evidence').glob('r2-two-views-*.json'))[-1].read_text('utf8'))
    r1_full = json.loads((HERE.parent / 'drug-trace-history-live-compare-20261023/'
                          'evidence/full-compare-r1.json').read_text('utf8'))
    missing_pairs = json.loads(Path(r1_full['private_details_path']).read_text('utf8'))['missing_pairs']
    private_dir = Path(audit['private_dir'])

    # index rows for code -> source records (missing scope)
    by_code = {}
    for r in rows['sql04_missing']:
        by_code.setdefault(str(r['T_TRACE_CODE']), []).append(r)

    # ---- U-classification of all 5077 pairs ----
    classification = []
    counts = Counter()
    reasons = Counter()
    for pair in missing_pairs:
        legacy_bill, code, n = pair[0], pair[1], pair[2]
        recs = by_code.get(code, [])
        exact = [r for r in recs if str(r['PRESC_DETAILID']) == legacy_bill]
        item = {'bill': legacy_bill, 'code': code, 'n': n,
                'source_records_for_code': len(recs)}
        if exact:
            r = exact[0]
            hit = int(r['MASTER_JOIN_HIT'] or 0)
            inout = str(r['IN_OUT_TYPE'] or '')
            if hit == 1 and inout == 'OUT':
                u, why = 'U01', 'visible-in-view-expected (time-point/view check needed)'
            elif hit == 1:
                u, why = 'U01', 'master-joined but IN_OUT_TYPE=%s (view OUT filter)' % inout
            else:
                u, why = 'U02', 'record exists, 3-key master JOIN miss'
            item.update({'record_id': str(r['RECORD_ID']), 'in_out': inout,
                         'join_hit': hit, 'split': str(r.get('SPLIT_FLAG')),
                         'deleted': str(r.get('IS_DELETED'))})
        elif recs:
            u, why = 'U03', 'code present under other PRESC_DETAILID(s); legacy id absent'
            item['current_line_ids'] = sorted({str(r['PRESC_DETAILID']) for r in recs})[:5]
        else:
            u, why = 'U04', 'code not found in source (batch complete, no truncation)'
        item['u'] = u
        item['why'] = why
        classification.append(item)
        counts[u] += n  # keep original multiplicity
        reasons[(u, why.split(' (')[0])] += n
    total = sum(counts.values())
    assert total == 5077, total

    # ---- dup group cause analysis ----
    detail_r2 = json.loads(Path(prev['private_details_path']).read_text('utf8'))
    dup_pairs = {(r['bill'], r['code']) for r in detail_r2['dup_pair_view_rows']}
    dup_by_code = {}
    for r in rows['sql04_dup']:
        dup_by_code.setdefault(str(r['T_TRACE_CODE']), []).append(r)
    dup_analysis = []
    for (bill, code) in sorted(dup_pairs):
        recs = dup_by_code.get(code, [])
        exact = [r for r in recs if str(r['PRESC_DETAILID']) == bill]
        cts = sorted([as_dt(r['CREATE_TIME']) for r in exact if as_dt(r['CREATE_TIME'])])
        delta = (cts[-1] - cts[0]).total_seconds() if len(cts) >= 2 else None
        dup_analysis.append({
            'bill': bill, 'code': code, 'records_for_code': len(recs),
            'exact_records': len(exact),
            'split_flags': sorted({str(r.get('SPLIT_FLAG')) for r in exact}),
            'in_out': sorted({str(r.get('IN_OUT_TYPE')) for r in exact}),
            'deleted': sorted({str(r.get('IS_DELETED')) for r in exact}),
            'join_hits': sorted({int(r['MASTER_JOIN_HIT'] or 0) for r in exact}),
            'create_span_seconds': delta,
            'create_times': [str(r['CREATE_TIME']) for r in exact]})
    dup_agg = {
        'pairs': len(dup_analysis),
        'exact_records_2': sum(1 for d in dup_analysis if d['exact_records'] == 2),
        'exact_records_other': {str(k): v for k, v in
                                Counter(d['exact_records'] for d in dup_analysis
                                        if d['exact_records'] != 2).items()},
        'split_flag_sets': {json.dumps(k): v for k, v in
                            Counter(tuple(d['split_flags']) for d in dup_analysis).items()},
        'in_out_sets': {json.dumps(k): v for k, v in
                        Counter(tuple(d['in_out']) for d in dup_analysis).items()},
        'deleted_sets': {json.dumps(k): v for k, v in
                         Counter(tuple(d['deleted']) for d in dup_analysis).items()},
        'join_hit_sets': {json.dumps(k): v for k, v in
                          Counter(tuple(d['join_hits']) for d in dup_analysis).items()},
        'create_span_buckets': {},
    }
    for d in dup_analysis:
        s = d['create_span_seconds']
        b = ('same_second' if s == 0 else
             'lt60s' if s is not None and s < 60 else
             'lt1h' if s is not None and s < 3600 else
             'gte1h' if s is not None else 'unknown')
        dup_agg['create_span_buckets'][b] = dup_agg['create_span_buckets'].get(b, 0) + 1
    (private_dir / 'dup-analysis.json').write_text(
        json.dumps(dup_analysis, ensure_ascii=False, indent=2), encoding='utf8')
    (private_dir / 'pair-classification.json').write_text(
        json.dumps(classification, ensure_ascii=False, indent=2), encoding='utf8')

    # ---- daily coverage aggregate ----
    daily = audit.get('daily_coverage', [])
    tot = {'days': 0, 'join_rows': 0, 'masters_no_out': 0, 'out_rows': 0}
    per_source = Counter()
    for day in daily:
        if not day.get('ok', True):
            continue
        tot['days'] += 1
        for r in day['master']:
            tot['join_rows'] += int(r['JOIN_ROWS'])
            tot['masters_no_out'] += int(r['MASTERS_NO_OUT'])
            tot['out_rows'] += int(r['OUT_ROWS'])
            per_source[str(r['PRESC_SOURCE'])] += int(r['JOIN_ROWS'])
    report = {
        'analyzed_at': datetime.now(timezone.utc).isoformat(),
        'audit_run_id': audit['run_id'],
        'identity': audit['identity'],
        'u_counts_rows_with_multiplicity': dict(counts),
        'u_reasons_rows': {' | '.join(k): v for k, v in sorted(reasons.items())},
        'conservation': {'total': total, 'ok': total == 5077},
        'distinct_pairs_u': dict(Counter(x['u'] for x in classification)),
        'dup_cause_aggregate': dup_agg,
        'daily_coverage_totals': tot,
        'daily_coverage_by_presc_source_join_rows': dict(per_source),
        'daily_mode': audit['daily_mode'],
        'private_paths': {'pairs': str(private_dir / 'pair-classification.json'),
                          'dup': str(private_dir / 'dup-analysis.json')},
        'source_writes': 0, 'uploads': 0,
    }
    (HERE / 'evidence' / ('source-classification-' + audit['run_id'] + '.json')).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
