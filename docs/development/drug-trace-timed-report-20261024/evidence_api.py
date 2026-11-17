"""Register standing collect + send evidence via the audited API (container-side).

Reads token from stdin. Honest references: forward-only scope from 2026-10-24,
10-12..10-23 gap excluded pending vendor confirmation, NTP/CA deferrals documented.
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TOKEN = sys.stdin.readline().strip()
BASE = 'http://localhost:8000/api/v1'
DEST_ID = '345df8f4-410e-4c89-a258-a34957eb4151'
PROFILE = 'alihealth_yljg_retail_v1'
CONFIG_RELEASE = '2e138e50-2be7-4574-8e48-61264be8946d'
GMT8 = timezone(timedelta(hours=8))

collect_items = [
    {"key": "EXT-01", "evidence_ref": "dedicated reader BLYB_DRUGTRACE re-verified 2026-10-23/24 "
     "(rootcause-r2 + timed rounds): service his, CREATE SESSION only, two non-grantable view SELECT grants",
     "provider": "user-executed DBA SQL; live reader verification receipts"},
    {"key": "EXT-02", "evidence_ref": "user-executed r10 views; definitions token-identical to r10 draft "
     "re-verified 2026-10-23; batch normal-outpatient branch only, unknown branches wait",
     "provider": "docs/development/drug-trace-history-rootcause-r2/RESULT.md"},
]
send_items = [
    {"key": "EXT-03", "evidence_ref": "DMZ->platform HTTPS verified repeatedly; real send receipt 2026-10-22",
     "provider": "one-bill trial receipts"},
    {"key": "EXT-04", "evidence_ref": "strict mTLS active; formal CA deferred by user (2026-10-22 deferral "
     "record stands); uploadretail real-send succeeded 2026-10-22", "provider": "trial receipts; user deferral"},
    {"key": "EXT-05", "evidence_ref": "DMZ synced via <relay-net-host> (chrony); <prod-host> NTP still unsynchronized - "
     "standing risk documented, sends fail visibly on clock errors, user instructed start 2026-10-24",
     "provider": "docs/deployment/drug-trace-ntp-20261023/README.md"},
    {"key": "EXT-06", "evidence_ref": "AppKey/AppSecret/ref_ent_id registered; platform accepted real "
     "uploadretail call 2026-10-22 (result 3/51)", "provider": "platform credentials registry; trial receipt"},
    {"key": "EXT-07", "evidence_ref": "SIGNATURE_VERIFIED=True shipped with release; platform accepted "
     "implementation signature in real send", "provider": "SIGNATURE-QUALIFICATION"},
    {"key": "EXT-08", "evidence_ref": "bill_type 321 / query_type A dictionary revisions approved "
     "r10-normal-20261022-r1; proven by real 3/51 receipt", "provider": "source-deploy dictionary receipts"},
    {"key": "EXT-10", "evidence_ref": "forward-only scope from 2026-10-24 00:00 GMT+8; 2026-10-12..23 gap "
     "NOT sent pending old-vendor/pharmacy confirmation; manual history files end 2026-10-11",
     "provider": "user scheduled-report instruction 2026-10-24; E08' open item documented"},
    {"key": "EXT-12", "evidence_ref": "daily batch window; pharmacy whitelist enumerated from live view "
     "2026-10-24; max_bills 80000 over 30 days; revocable via evidence revoke API",
     "provider": "user instruction 2026-10-24 (定时上报)"},
]
scope = {"window_begin": "2026-10-24T00:00:00+08:00",
         "window_end": "2026-11-23T00:00:00+08:00",
         "pharmacies": ["050121", "050102", "050101", "050116", "050117"],
         "max_bills": 80000}
valid_until = "2026-11-22T00:00:00+08:00"
out = {"calls": []}


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
        headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            st, data = r.status, json.load(r).get('data')
    except urllib.error.HTTPError as exc:
        try:
            data = json.load(exc)
        except Exception:
            data = {}
        err = data.get('error') or {}
        out['calls'].append({'method': method, 'path': path, 'status': exc.code,
                             'error': err.get('code')})
        raise RuntimeError(f'API rejected {path}: {err.get("code")} {str(err.get("message"))[:200]}')
    out['calls'].append({'method': method, 'path': path, 'status': st})
    return data


try:
    out['collect'] = call('POST', '/drug-trace/evidence', {
        'profile': PROFILE, 'capability': 'collect', 'items': collect_items,
        'valid_until': (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()})
    out['send'] = call('POST', '/drug-trace/evidence', {
        'profile': PROFILE, 'capability': 'send', 'destination_id': DEST_ID,
        'config_release_id': CONFIG_RELEASE, 'items': send_items,
        'scope': scope, 'valid_until': valid_until})
    st, ready = 'GET', call('GET', '/drug-trace/readiness')
    out['readiness'] = ready
    out['ok'] = True
except Exception as exc:
    out['ok'] = False
    out['error'] = str(exc)[:400]
print('__TIMED_RESULT__' + json.dumps(out, ensure_ascii=False, default=str) + '__END__')
