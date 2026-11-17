import json
import subprocess


def run(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return {'rc': p.returncode, 'out': p.stdout.strip()[:12000]}


sql = """SELECT json_build_object(
'errors',(SELECT json_agg(row_to_json(x)) FROM (SELECT job_type,state,last_error->>'code' code,left(last_error->>'message',160) message,count(*) n FROM job WHERE created_at>='2026-10-25 09:57:00' AND state='FAILED' GROUP BY 1,2,3,4) x),
'http429',(SELECT json_agg(row_to_json(x)) FROM (SELECT response_snapshot->'error'->>'code' code, response_snapshot->'error'->>'reason' reason,count(*) n FROM delivery_attempt WHERE http_status=429 GROUP BY 1,2) x),
'submissions',(SELECT json_object_agg(state,n) FROM (SELECT state,count(*) n FROM submission GROUP BY state) x),
'running_jobs',(SELECT json_agg(row_to_json(x)) FROM (SELECT job_type,state,count(*) n FROM job WHERE state IN ('RUNNING','PENDING') GROUP BY 1,2) x)
)"""
out = {'service': run(['systemctl','show','blyb-daily-upload.service','-p','ActiveState','-p','SubState']),
       'db': run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-c',sql]),
       'images': run(['docker','images','--format','{{.Repository}}:{{.Tag}} {{.ID}}']),
       'current': run(['readlink','-f','/opt/blyb/current']),
       'chrony':run(['chronyc','-n','sources']),
       'disk':run(['df','-h','/opt','/home'])}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
