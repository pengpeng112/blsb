"""RT-04 baseline generator: forbidden-files SHA256 manifest (ad hoc tool, keep in baseline dir)."""
import hashlib
import os

root = r'F:\python\blyb'

targets = [
    r'implementation/deploy/inf_pusher',
    r'implementation/deploy/inf_watchdog',
    r'implementation/plugins/destinations/alihealth_drugtrace',
    r'docs/development/drug-trace-timed-report-20261024',
]
files = [
    r'implementation/apps/admin_web/src/views/DrugTrace.vue',
    r'implementation/apps/admin_web/src/views/drugTrace.css',
    r'implementation/apps/admin_web/src/views/drugTraceDisplay.ts',
    r'implementation/docs/audit/2026-10-01/test_review_probes.py',
    r'implementation/apps/admin_web/package.json',
    r'implementation/apps/admin_web/package-lock.json',
]


def sha256_file(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main():
    lines = []
    for d in targets:
        dp = os.path.join(root, d)
        if not os.path.isdir(dp):
            lines.append('MISSING-DIR ' + d.replace(os.sep, '/'))
            continue
        for dirpath, dirnames, filenames in os.walk(dp):
            dirnames[:] = [x for x in dirnames if x not in ('__pycache__', '.pytest_cache', 'node_modules')]
            for fn in sorted(filenames):
                if fn.endswith('.pyc'):
                    continue
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, root).replace(os.sep, '/')
                lines.append(sha256_file(fp) + '  ' + rel)
    for f in files:
        fp = os.path.join(root, f)
        if not os.path.isfile(fp):
            lines.append('MISSING-FILE ' + f.replace(os.sep, '/'))
            continue
        lines.append(sha256_file(fp) + '  ' + f.replace(os.sep, '/'))
    mdir = os.path.join(root, 'implementation/migrations')
    mig = sorted(f for f in os.listdir(mdir) if f[0].isdigit()) if os.path.isdir(mdir) else []
    for fn in mig:
        fp = os.path.join(mdir, fn)
        if os.path.isfile(fp):
            lines.append(sha256_file(fp) + '  implementation/migrations/' + fn)
    out = os.path.join(root, 'docs/development/platform-rectification-20261111/revision-2/baseline/rt04_forbidden_before.txt')
    with open(out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(lines) + '\n')
    print('wrote', out, len(lines), 'entries')
    print('migration files:', len(mig))


if __name__ == '__main__':
    main()
