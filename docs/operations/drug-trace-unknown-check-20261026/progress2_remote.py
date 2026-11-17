from pathlib import Path
from collections import Counter
import json
folder=Path('/root/blyb-maintenance-backups/unknown-review-20261026-r2')
rows=[]
if (folder/'results.jsonl').exists():
    for line in (folder/'results.jsonl').read_text().splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if event.get('event')=='result':rows.append(event['data'])
out={'checked':len(rows),'pairs':{a+'->'+b:n for (a,b),n in Counter((r['local_state'],r['platform_state']) for r in rows).items()},
     'platform_calls':sum(r['platform_calls'] for r in rows),'last_checked_utc':rows[-1]['checked_utc'] if rows else None,
     'http':dict(sum((Counter(r['http']) for r in rows),Counter())),'final':json.loads((folder/'final.json').read_text()) if (folder/'final.json').exists() else None}
if out['final']:out['rows']=rows
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
