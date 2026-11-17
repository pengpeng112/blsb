from pathlib import Path
import base64
HERE=Path(__file__).resolve().parent
payload=base64.b64encode((HERE/'pause_remote.py').read_bytes()).decode()
code="""import base64,json,traceback
try:exec(compile(base64.b64decode(PAYLOAD),'pause_remote','exec'))
except Exception as e:
 print('__MAINTENANCE__'+json.dumps({'error_type':type(e).__name__,'reason':str(e)[:800],'frames':[[f.name,f.lineno] for f in traceback.extract_tb(e.__traceback__)]})+'__END__')
 raise SystemExit(1)
""".replace('PAYLOAD',repr(payload))
(HERE/'pause_retry_remote.py').write_text(code,encoding='utf8')
