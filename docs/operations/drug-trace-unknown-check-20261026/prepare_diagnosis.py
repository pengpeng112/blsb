from pathlib import Path
HERE=Path(__file__).resolve().parent
source=(HERE/'sample_remote.py').read_text('utf8')
start=source.index('code=');end=source.index('\np=subprocess.run',start)
source=source[:start]+'code='+repr((HERE/'diagnose_container.py').read_text('utf8'))+source[end:]
(HERE/'diagnose_remote.py').write_text(source,encoding='utf8')
