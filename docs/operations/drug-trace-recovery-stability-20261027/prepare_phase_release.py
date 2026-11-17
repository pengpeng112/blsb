"""Stage an immutable UI-only successor to the deployed stability backend."""
from pathlib import Path
HERE=Path(__file__).resolve().parent
OLD='stability-20261027T030000Z-r2';NEW='stability-20261027T064000Z-r3'
s=(HERE/'build_release.py').read_text(encoding='utf8')
assert s.count("BASE=IMPL/'var/flow-20261026T132000Z'")==1
s=s.replace("BASE=IMPL/'var/flow-20261026T132000Z'","BASE=IMPL/'var/"+OLD+"'")
s=s.replace("RID='"+OLD+"'","RID='"+NEW+"'")
s=s.replace("'base_release':'flow-20261026T132000Z'","'base_release':'"+OLD+"'")
allowed={'apps/admin_web/src/views/DrugTrace.vue','apps/admin_web/src/views/drugTraceDisplay.ts','apps/admin_web/tests/drugTraceDisplay.test.mjs'}
s=s[:s.index('\nallowed=')]+'\nallowed='+repr(allowed)+"\nassert set(m['delta']['files'])==allowed,'NON_PRESENTATION_DELTA'\n"
compile(s,'ui-only-builder','exec');(HERE/'phase_build_release.py').write_text(s,encoding='utf8')
t=(HERE/'transfer_release.py').read_text(encoding='utf8').replace(OLD,NEW)
compile(t,'ui-only-transfer','exec');(HERE/'phase_transfer_release.py').write_text(t,encoding='utf8')
print('Prepared offline UI-only successor; no build/deployment yet')
