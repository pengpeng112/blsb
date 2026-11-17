"""Inspect literal systemd printf format; public dummy only, no real token."""
import json
import hashlib
from pathlib import Path
import re
import subprocess


def output(args):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    return p.returncode, p.stdout.decode('utf8', 'replace')


out = {}
rc, text = output(['systemctl', 'show', 'blyb-daily-upload.service', '--property=ExecStart', '--value'])
unit = Path('/etc/systemd/system/blyb-daily-upload.service').read_text()
out['unit_sha256'] = hashlib.sha256(unit.encode()).hexdigest()
source_match = re.search(r'printf\s+"([^"\r\n]+)"', unit)
effective_match = re.search(r'printf\s+"([^"\r\n]+)"', text)
out['show_exit_code'] = rc
out['source_unescaped_percent_s'] = bool(source_match and '%s' in source_match.group(1) and '%%s' not in source_match.group(1))
out['source_escaped_percent_s'] = bool(source_match and '%%s' in source_match.group(1))
out['effective_format_found'] = bool(effective_match)
if not effective_match:
    # systemctl show can strip shell quotes in formatted argv display.
    effective_match = re.search(r'printf\s+([^\s;]+)', text)
if effective_match:
    fmt = effective_match.group(1)
    # Whitelist static format strings only. Never return full ExecStart/env.
    if re.fullmatch(r'(?:/bin/(?:ba)?sh|%s)(?:\\+n)?', fmt):
        out['effective_printf_format'] = fmt
        fake = subprocess.run(['/bin/bash', '-c', 'printf "' + fmt + '" "PUBLIC_DUMMY"'], stdout=subprocess.PIPE)
        out['dummy_stripped_equals_input'] = fake.stdout.decode().strip() == 'PUBLIC_DUMMY'
        out['dummy_equals_shell_path'] = fake.stdout.decode().strip() in ('/bin/bash', '/bin/sh', '/bin/bash\\n', '/bin/sh\\n')
        out['dummy_ends_newline'] = fake.stdout.endswith(b'\n')
        out['dummy_ends_literal_backslash_n'] = fake.stdout.endswith(b'\\n')
    else:
        out['format_whitelist_match'] = False
print('__HEALTH_JSON__' + json.dumps(out, ensure_ascii=True) + '__HEALTH_END__')
