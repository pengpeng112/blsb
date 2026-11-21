"""Maintenance-tool contracts; separate from application tests/ totals."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


session = module('ai_session')
documents = module('project_docs')


def start(root, task='T1', scope='implementation/apps/core_api/app.py'):
    return session.start(root, task=task, tool='test-tool', model='unknown', scopes=[scope])


def test_other_tool_reads_checkpoint_and_reclaims_only_after_handoff(tmp_path):
    first = start(tmp_path)
    session.update(tmp_path, first['session_id'], summary='actual checkpoint')
    observed = session.records(tmp_path)[0]
    assert observed['summary'] == 'actual checkpoint' and observed['status'] == 'running'
    with pytest.raises(ValueError, match='conflict'):
        start(tmp_path, 'T2')
    handoff = tmp_path / 'handoff.md'
    handoff.write_text('Remaining: permission checks', encoding='utf-8')
    session.update(tmp_path, first['session_id'], result='handed_off', summary='continue next', handoff='handoff.md')
    second = start(tmp_path, 'T2')
    assert second['session_id'] != first['session_id']
    assert len(session.records(tmp_path)) == 2


def test_parent_child_case_insensitive_scope_conflict(tmp_path):
    start(tmp_path, scope='implementation/apps')
    with pytest.raises(ValueError, match='conflict'):
        start(tmp_path, 'T2', 'Implementation/Apps/core_api/app.py')


def test_independent_files_can_be_claimed(tmp_path):
    start(tmp_path)
    start(tmp_path, 'T2', 'implementation/apps/edge_agent/service.py')
    assert len(session.records(tmp_path)) == 2


def test_same_task_is_not_claimed_twice_even_with_disjoint_files(tmp_path):
    start(tmp_path)
    with pytest.raises(ValueError, match='conflict'):
        start(tmp_path, scope='README.md')


def test_two_processes_racing_for_same_scope_have_one_winner(tmp_path):
    command = [sys.executable, str(ROOT / 'scripts/ai_session.py'), '--workspace', str(tmp_path),
               'start', '--tool', 'test', '--model', 'unknown', '--scope', 'apps']
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(subprocess.run, command + ['--task', f'T{i}'], capture_output=True) for i in (1, 2)]
        results = [f.result() for f in futures]
    assert sorted(r.returncode for r in results) == [0, 2]
    assert len(session.records(tmp_path)) == 1


@pytest.mark.parametrize('scope', ['../outside', '..', ''])
def test_scope_cannot_escape_workspace(tmp_path, scope):
    with pytest.raises(ValueError):
        start(tmp_path, scope=scope)


def test_bad_handoff_does_not_release_claim(tmp_path):
    first = start(tmp_path)
    with pytest.raises(ValueError):
        session.update(tmp_path, first['session_id'], result='handed_off', summary='invalid', handoff='../outside.md')
    assert session.records(tmp_path)[0]['status'] == 'running'


def test_closed_session_cannot_be_reopened_by_checkpoint(tmp_path):
    first = start(tmp_path)
    (tmp_path / 'handoff.md').write_text('Verified outcome', encoding='utf-8')
    session.update(tmp_path, first['session_id'], result='completed', summary='verified', handoff='handoff.md')
    with pytest.raises(ValueError, match='closed'):
        session.update(tmp_path, first['session_id'], summary='reopen')


def test_claim_is_not_released_without_handoff(tmp_path):
    first = start(tmp_path)
    with pytest.raises(ValueError, match='handoff'):
        session.update(tmp_path, first['session_id'], result='completed', summary='claimed completion')
    assert session.records(tmp_path)[0]['status'] == 'running'


@pytest.mark.parametrize('output', ['README.md', '.ai/sessions/other.json'])
def test_context_export_cannot_overwrite_project_or_registry_files(output):
    r = subprocess.run([sys.executable, str(ROOT / 'scripts/context_packet.py'),
                        '--output', str(ROOT / output)], capture_output=True)
    assert r.returncode == 2


def test_document_link_checker_accepts_parentheses_and_ignores_code_examples(tmp_path):
    target = tmp_path / 'spec(draft).pdf'
    target.write_bytes(b'synthetic')
    (tmp_path / 'README.md').write_text('[source](spec(draft).pdf)\n```\n[x](missing.md)\n```', encoding='utf-8')
    assert documents.broken_links(tmp_path) == []
    target.unlink()
    assert documents.broken_links(tmp_path) == [{'file': 'README.md', 'target': 'spec(draft).pdf'}]


def test_legacy_archive_links_do_not_hide_new_active_broken_links(tmp_path):
    archived = tmp_path / '归档'
    archived.mkdir()
    (archived / 'old.md').write_text('[old](missing.md)', encoding='utf-8')
    (tmp_path / 'README.md').write_text('[new](missing.md)', encoding='utf-8')
    assert documents.broken_links(tmp_path) == [{'file': 'README.md', 'target': 'missing.md'}]


def test_invalid_test_scope_is_rejected_by_startup_wrapper():
    r = subprocess.run(['powershell', '-NoProfile', '-File', str(ROOT / 'project.ps1'),
                        '-Action', 'Test', '-TestPath', '../apps/core_api', '-DryRun'], capture_output=True)
    assert r.returncode != 0


def test_startup_dry_run_uses_project_path_from_other_directory(tmp_path):
    r = subprocess.run(['powershell', '-NoProfile', '-File', str(ROOT / 'project.ps1'),
                        '-Action', 'Dev', '-DryRun'], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    plan = json.loads(r.stdout)
    assert Path(plan['WorkingDirectory']).resolve() == (ROOT / 'implementation').resolve()
    assert plan['Arguments'] == ['run_dev.py']


def test_startup_propagates_pytest_missing_path_exit_code():
    r = subprocess.run(['powershell', '-NoProfile', '-File', str(ROOT / 'project.ps1'),
                        '-Action', 'Test', '-PythonExe', sys.executable,
                        '-TestPath', 'tests/__maintenance_nonexistent__.py'], capture_output=True)
    assert r.returncode == 4


def test_session_status_summary_default_and_stale_warning(tmp_path):
    first = start(tmp_path, 'T-OLD', 'README.md')
    # Backdate updated_at by 30h to trigger stale_warning
    rec_path = tmp_path / '.ai' / 'sessions' / f"{first['session_id']}.json"
    data = json.loads(rec_path.read_text(encoding='utf-8'))
    data['updated_at'] = '2026-01-01T00:00:00+00:00'
    rec_path.write_text(json.dumps(data), encoding='utf-8')
    for i in range(7):
        s = start(tmp_path, f'T-DONE-{i}', f'file_{i}.md')
        h = tmp_path / f'h_{i}.md'
        h.write_text('done', encoding='utf-8')
        session.update(tmp_path, s['session_id'], result='completed', summary=f'ok {i}', handoff=f'h_{i}.md')

    summary = session.status_summary(tmp_path)
    assert summary['total'] == 8
    assert summary['counts']['running'] == 1
    assert summary['counts']['completed'] == 7
    assert len(summary['active']) == 1 and summary['active'][0].get('stale_warning') is True
    assert len(summary['recent_closed']) == 5
    full = session.status_summary(tmp_path, show_all=True)
    assert isinstance(full, list) and len(full) == 8


def test_historical_snapshot_dirs_are_skipped_by_link_checker(tmp_path):
    (tmp_path / 'docs' / 'ai' / 'archive').mkdir(parents=True)
    (tmp_path / 'docs' / 'ai' / 'archive' / 'old.md').write_text('[x](missing.md)', encoding='utf-8')
    (tmp_path / 'docs' / 'operations' / 'op1' / 'baselines').mkdir(parents=True)
    (tmp_path / 'docs' / 'operations' / 'op1' / 'baselines' / 'snap.md').write_text('[y](missing.md)', encoding='utf-8')
    (tmp_path / 'README.md').write_text('[active](missing.md)', encoding='utf-8')
    assert documents.broken_links(tmp_path) == [{'file': 'README.md', 'target': 'missing.md'}]


@pytest.mark.parametrize('action', ['Showcase', 'TestFast', 'TestWeb', 'Health'])
def test_new_startup_actions_dry_run(action):
    r = subprocess.run(['powershell', '-NoProfile', '-File', str(ROOT / 'project.ps1'),
                        '-Action', action, '-DryRun'], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    plan = json.loads(r.stdout)
    assert 'WorkingDirectory' in plan and 'Executable' in plan


def test_root_hygiene_and_state_bloat_warnings(tmp_path):
    (tmp_path / 'README.md').write_text('# ok', encoding='utf-8')
    (tmp_path / '新需求说明.xlsx').write_bytes(b'xls')
    (tmp_path / 'stray.out').write_text('tmp', encoding='utf-8')
    warns = documents.root_hygiene_warnings(tmp_path)
    by_path = {w['path']: w['suggestion'] for w in warns}
    assert 'README.md' not in by_path
    assert '数据上报/' in by_path['新需求说明.xlsx']
    assert 'implementation/var/' in by_path['stray.out']

    ai_dir = tmp_path / 'docs' / 'ai'
    ai_dir.mkdir(parents=True)
    (ai_dir / 'STATE.md').write_text('\n'.join(f'line {i}' for i in range(250)), encoding='utf-8')
    bloat = documents.state_bloat_warnings(tmp_path, max_lines=200)
    assert len(bloat) == 1 and bloat[0]['file'] == 'docs/ai/STATE.md' and bloat[0]['lines'] == 250


