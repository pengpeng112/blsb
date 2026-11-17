"""Current rate settings and aggregated journal counts; no request bodies."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import json,subprocess,os,re
cfg=json.loads(Path('/etc/blyb-dmz/forwarder.json').read_text())
out={'config':{k:cfg.get(k) for k in ['rate_per_minute','rate_burst','max_concurrent','upstream_host']},'periods':{}}
for name,begin,end in [('old_failure','2026-10-25 12:28:00','2026-10-25 13:31:11'),('after_pacing_fix','2026-10-25 13:43:00','2026-10-25 13:53:41'),('manual_review','2026-10-26 07:15:00','2026-10-26 10:19:00')]:
 p=subprocess.run(['journalctl','-u','blyb-dmz-forwarder.service','--since',begin,'--until',end,'-o','json','--no-pager'],capture_output=True,text=True,timeout=40,env={**os.environ,'TZ':'UTC'})
 assert p.returncode==0,'JOURNAL_READ_FAILED'
 counts=Counter();ips=Counter();minutes=Counter();seconds=Counter();first=None;last=None
 for line in p.stdout.splitlines():
  try:entry=json.loads(line)
  except ValueError:continue
  message=entry.get('MESSAGE','')
  if not isinstance(message,str):continue
  kind='reject' if 'reject rid=' in message else 'forward' if 'forward rid=' in message else ''
  if not kind:continue
  status=re.search(r'status=(\d+)',message);reason=re.search(r'reason=([\w-]+)',message);ip=re.search(r'ip=([\d.]+)',message)
  counts[kind+':'+(status.group(1) if status else '?')+(':'+reason.group(1) if reason else '')]+=1
  if ip:ips[ip.group(1)]+=1
  stamp=int(entry.get('__REALTIME_TIMESTAMP','0'))/1_000_000
  if stamp:
   key=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
   minutes[key[:16]]+=1;seconds[key[:19]]+=1
   first=key if first is None or key<first else first;last=key if last is None or key>last else last
 out['periods'][name]={'counts':dict(counts),'client_ips':dict(ips),'peak_per_utc_minute':minutes.most_common(5),
  'peak_requests_per_second':max(seconds.values(),default=0),'first_utc':first,'last_utc':last,'total_attempts':sum(counts.values())}
out['captured_utc']=datetime.now(timezone.utc).isoformat()
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
