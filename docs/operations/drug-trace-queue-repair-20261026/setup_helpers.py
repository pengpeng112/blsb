"""Derive deployment helpers; secrets remain in the existing pinned channel."""
from pathlib import Path

HERE=Path(__file__).resolve().parent
OLD=HERE.parent/'drug-trace-ntp-dashboard-20261025'
rid='queue-20261026T010000Z-r2'
code=(OLD/'upgrade_remote.py').read_text('utf8')
code=code.replace("rid='ui-20261025T140000Z'",'rid='+repr(rid))
code=code.replace("!='src-20261022T103000Z'","!='ui-20261025T140000Z'")
code=code.replace("+rid+'-r2'","+rid+'-r1'")
(HERE/'upgrade_remote.py').write_text(code,encoding='utf8')
code=(OLD/'upgrade_status_remote.py').read_text('utf8').replace('upgrade-ui-20261025T140000Z-r2','upgrade-'+rid+'-r1')
(HERE/'upgrade_status_remote.py').write_text(code,encoding='utf8')
for name in ['install_driver.py','install_driver_remote.py']:
    code=(OLD/name).read_text('utf8').replace('ui-20261025T140000Z',rid)
    code=code.replace('blyb-driver-dashboard-install.py','blyb-driver-queue-install.py')
    code=code.replace('1f275f0b20fb58ddc8319347d65981d3f253e85b1005ff2853ea7392e2726df3','fba1ef3026c8b7e0b4e6816fe51bafa04b2957d66de0d1667f9abee591cb0f9f')
    code=code.replace('f89c0c81f5448818d4d05b58145d016621838f45710e4cf00b5ea76a7a5d33f0','4bb3cb60258a88ac1cf290895b9f5d5674ac1d0994221077ac63006d6d396b06')
    (HERE/name).write_text(code,encoding='utf8')
(HERE/'health_remote.py').write_bytes((OLD/'health_remote.py').read_bytes())
print('Prepared helpers for '+rid)
