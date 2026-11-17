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
