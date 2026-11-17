"""Local startup/execution probes only; no model turn, source DB or business data."""
from pathlib import Path
import json
import os
import queue
import subprocess
import sys
import threading
import time

ROOT = Path('C:/Tools/CodexCLI')
NODE = Path('C:/Users/Administrator/AppData/Local/Microsoft/WinGet/Packages/OpenJS.NodeJS.22_Microsoft.Winget.Source_8wekyb3d8bbwe/node-v22.23.2-win-x64/node.exe')
BIN = ROOT / 'node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin'
JS = ROOT / 'node_modules/@openai/codex/bin/codex.js'
OUT = Path(__file__).resolve().parent


def reader(stream, q):
    for line in iter(stream.readline, ''):
        try:
            q.put(json.loads(line))
        except json.JSONDecodeError:
            pass


def probe():
    evidence = {'no_model_turn': True, 'no_business_data': True}
    env = os.environ.copy()
    env['PATH'] = str(ROOT) + os.pathsep + env.get('PATH', '')
    version = subprocess.run([str(NODE), str(JS), '--no-daemon', '--version'], capture_output=True, text=True, timeout=15, env=env)
    evidence['no_daemon_flag_version'] = {'exit': version.returncode, 'stdout': version.stdout.strip()}
    if version.returncode or '0.165.0' not in version.stdout:
        raise RuntimeError('no-daemon version probe failed')
    help_result = subprocess.run([str(BIN / 'codex-code-mode-host.exe'), '--help'], capture_output=True, text=True, timeout=15, env=env)
    evidence['host_help'] = {'exit': help_result.returncode, 'path': str(BIN / 'codex-code-mode-host.exe')}
    if help_result.returncode:
        raise RuntimeError('host executable failed')
    host = subprocess.Popen([str(BIN / 'codex-code-mode-host.exe'), '--listen', 'stdio://'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        time.sleep(1)
        evidence['host_stdio_startup'] = {'pid': host.pid, 'running': host.poll() is None}
        if host.poll() is not None:
            raise RuntimeError('host did not stay running')
    finally:
        if host.poll() is None:
            host.terminate()
        host.communicate(timeout=10)
    server = subprocess.Popen([str(NODE), str(JS), 'app-server', '--listen', 'stdio://'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', env=env, cwd=str(OUT))
    q = queue.Queue()
    threading.Thread(target=reader, args=(server.stdout, q), daemon=True).start()
    threading.Thread(target=lambda: [None for _ in server.stderr], daemon=True).start()
    def send(message):
        server.stdin.write(json.dumps(message)+'\n')
        server.stdin.flush()
    def response(request_id):
        deadline = time.monotonic()+35
        while time.monotonic()<deadline:
            message=q.get(timeout=max(.1,deadline-time.monotonic()))
            if message.get('id')==request_id:
                if 'error' in message:
                    raise RuntimeError(f'RPC {request_id}: {message["error"]}')
                return message.get('result')
        raise RuntimeError('RPC timeout')
    try:
        send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'codex_local_repair_probe','version':'1.0'}}})
        response(1)
        send({'method':'initialized','params':{}})
        evidence['foreground_initialize'] = True
        send({'id':2,'method':'command/exec','params':{'command':[sys.executable,'-c','print("CODEX_LOCAL_EXEC_OK")'],'cwd':str(OUT),'sandboxPolicy':{'type':'dangerFullAccess'},'timeoutMs':15000}})
        result = response(2)
        evidence['local_command'] = result
        if result.get('exitCode')!=0 or result.get('stdout','').strip()!='CODEX_LOCAL_EXEC_OK':
            raise RuntimeError('foreground command probe failed')
    finally:
        if server.poll() is None:
            server.terminate()
        server.wait(timeout=10)
    return evidence


if __name__=='__main__':
    result=probe()
    (OUT/'runtime-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
