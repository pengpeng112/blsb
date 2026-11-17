from pathlib import Path
import hashlib,json,subprocess
expected={'apps/worker/engine.py': '6026ba086fb0a9a1a320fd0977097ff063ab21744c193140887787edab22caae', 'packages/connector_sdk/__init__.py': '67d7838ced05c9d2dbb852710337d56a2ca41b6caed0ad78d7ed1b75b77e90b6', 'packages/workflow_core/jobs.py': 'bd7dac77868f092ca380b3616e8590d90ff08f27a933f96aec2723e1fbe21922', 'plugins/destinations/alihealth_drugtrace/client.py': 'b774ccbe6c91859203a7b06812ab2729f3507513659dc1b84ceef080b66ca8ad', 'plugins/destinations/alihealth_drugtrace/protocol.py': 'e554c2eba9f981b6a58c7d8cdf9fbfd79831bd7317c032188dd6b04189655b01', 'plugins/destinations/alihealth_drugtrace/rate_control.py': 'a5dfbbd4e985beadf650993408e4ab61a20cfc48a941e22c7689f0d84008850c', 'plugins/destinations/alihealth_drugtrace/transport.py': '0a84ea481e1de1864bfecfbcbfbba1e559e6aa2ad91606b6922a64a8194b8b8b', 'plugins/domains/drug_trace/plugin.py': '8e857be8889d09d8f1475f9d5685d363103789cb609fc1c8b3c9f00426648cde', 'plugins/domains/drug_trace/service.py': 'c5ba8c3c10f48b6813c5fee7bfac4ef7f702611be05fd2ae3d98c08371d31242'}
code='from pathlib import Path;import hashlib,json;expected='+repr(expected)+';print(json.dumps({p:hashlib.sha256((Path("/app")/p).read_bytes()).hexdigest()==h for p,h in expected.items()}))'
p=subprocess.run(['docker','exec','<worker-container>','python','-c',code],capture_output=True,text=True,timeout=20)
assert p.returncode==0,'SOURCE_HASH_CHECK_FAILED'
out={'runtime_source_hashes':json.loads(p.stdout)}
assert all(out['runtime_source_hashes'].values())
out['driver_sha256']=hashlib.sha256(Path('/opt/blyb/tools/blyb-daily-upload.py').read_bytes()).hexdigest()
assert out['driver_sha256']=='cf967102e68c4978cc17ba4739af6860de60344404727f6cc8eedf1b4a851c39'
sql="SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x"
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=20)
assert p.returncode==0
out['states']=json.loads(p.stdout)
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
