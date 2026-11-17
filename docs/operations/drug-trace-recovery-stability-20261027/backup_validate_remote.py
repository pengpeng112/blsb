"""Read-only integrity/restore-plan verification of the final consistent backup."""
from pathlib import Path
from datetime import datetime,timezone
import json,subprocess

RID='stability-20261027T064000Z-r3'
assert Path('/opt/blyb/current').resolve().name==RID
private=Path('/root/blyb-maintenance-backups/upgrade-'+RID)
verified=json.loads((private/'ui-verified.json').read_text())
assert verified['all_4818_submissions_unchanged'] and verified['quota_unchanged']
result=json.loads((private/'result.json').read_text());assert result['exit_code']==result['timer_restart_exit']==0
backup=sorted(p for p in Path('<prod-path>/backups').iterdir() if p.is_dir())[-1]
assert backup.name.startswith('20261027T07'),'WRONG_BACKUP_WINDOW'
target=Path('/var/tmp/blyb-restore-validate-stability-20261027-r3')
assert not target.exists(),'VALIDATION_TARGET_NOT_EMPTY'
sql="SELECT json_build_object('ledger_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text||':'||snapshot_digest,',' ORDER BY id)) FROM submission),'quota',(SELECT sum(used) FROM drug_trace_send_quota))"
def snapshot():
    p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=25)
    assert p.returncode==0;return json.loads(p.stdout)
before=snapshot()
args=['bash','/opt/blyb/current/implementation/deploy/linux/restore.sh',str(backup),
      '--validate-only','--project','blyb-verify-stability-20261027','--data-root',str(target),
      '--etc-root',str(target/'etc-blyb'),'--release','/opt/blyb/releases/stability-20261027T030000Z-r2']
p=subprocess.run(args,capture_output=True,text=True,timeout=90)
(private/'restore-validate.log').write_text(p.stdout+p.stderr)
assert p.returncode==0,'RESTORE_PLAN_VALIDATION_FAILED'
assert '缺镜像' not in p.stdout,'RESTORE_IMAGE_MISSING'
assert '--validate-only' in p.stdout and not target.exists(),'TARGET_WAS_MODIFIED'
with (backup/'core.pg_dump').open('rb') as archive:
    toc=subprocess.run(['docker','exec','-i','blyb-db-1','pg_restore','--list'],stdin=archive,capture_output=True,timeout=30)
assert toc.returncode==0,'PG_DUMP_TOC_INVALID'
toc_text=toc.stdout.decode('utf8','replace')
assert all(name in toc_text for name in ('submission','receipt_event','drug_trace_daily_report','drug_trace_bill','schema_migrations'))
after=snapshot();assert before==after,'LIVE_LEDGER_CHANGED'
out={'backup':str(backup),'validate_only_exit':p.returncode,'archive_hashes_and_path_plan_verified':True,
     'backup_images_available':True,'pg_dump_toc_exit':toc.returncode,
     'pg_dump_toc_entries':sum(bool(line.strip()) and not line.startswith(';') for line in toc_text.splitlines()),
     'target_created':target.exists(),'production_ledger_unchanged':before==after,'snapshot':after,
     'full_restore_executed':False,'utc':datetime.now(timezone.utc).isoformat()}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
