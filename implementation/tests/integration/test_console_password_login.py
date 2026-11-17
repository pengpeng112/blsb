"""Synthetic password/session regressions; never connect to business databases."""
from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from contracts.api import hash_token
from contracts.passwords import hash_password, provision_account, verify_password
from core_db import make_engine, make_session_factory, run_migrations
from core_db.engine import utcnow
from core_db.models import AuthSubject, AuditEvent, ConsoleAccount, ConsoleSession, StorageVolume, Tenant

PASSWORD = 'Test-only-password-4826'
NEW_PASSWORD = 'Changed-test-password-4826'


@pytest.fixture()
def site(tmp_path, monkeypatch):
    import app_runtime
    from apps.core_api.app import create_app
    from apps.storage_service.app import create_app as create_storage
    engine = make_engine(f'sqlite:///{tmp_path}/auth.sqlite3')
    run_migrations(engine)
    sf = make_session_factory(engine)
    with sf() as db:
        db.add(Tenant(id='tenant-auth-a', code='auth-a', name='Synthetic A'))
        db.add(Tenant(id='tenant-auth-b', code='auth-b', name='Synthetic B'))
        db.flush()
        db.add(AuthSubject(id='legacy-machine', tenant_id='tenant-auth-a', kind='user', subject_code='bootstrap-admin',
                           roles=['platform_admin'], status='ACTIVE', token_hash=hash_token('test-legacy-machine')))
        db.add(StorageVolume(id='v-a', tenant_id='tenant-auth-a', name='Volume A', root_ref=str(tmp_path), backend='filesystem'))
        db.add(StorageVolume(id='v-b', tenant_id='tenant-auth-b', name='Volume B', root_ref=str(tmp_path), backend='filesystem'))
        db.commit()
        result = provision_account(db, tenant_code='auth-a', username='admin', password=PASSWORD,
                                   display_name='Synthetic administrator', roles=['platform_admin'])
        provision_account(db, tenant_code='auth-b', username='auditor-b', password=PASSWORD,
                          display_name='Synthetic B auditor', roles=['auditor'])
    core = TestClient(create_app(session_factory=sf, engine=engine, registry=app_runtime.load_registry(['metrics'])))
    storage = TestClient(create_storage(session_factory=sf, engine=engine, embedded_worker=False))
    try:
        yield {'core': core, 'storage': storage, 'sf': sf, 'engine': engine, 'subject': result['subject_id']}
    finally:
        core.close(); storage.close(); engine.dispose()


def signin(site, username='admin', password=PASSWORD):
    r = site['core'].post('/api/v1/auth/login', json={'username': username, 'password': password})
    assert r.status_code == 200, r.text
    assert r.headers['cache-control'] == 'no-store'
    return r.json()['data']


def headers(token):
    return {'Authorization': 'Bearer ' + token}


def test_password_hashes_are_salted_and_bounded():
    a, b = hash_password(PASSWORD), hash_password(PASSWORD)
    assert a != b and PASSWORD not in a
    assert verify_password(PASSWORD, a)
    assert not verify_password(PASSWORD+'wrong', a)
    assert not verify_password(PASSWORD, 'pbkdf2_sha256$999999999999$bad$bad')
    assert not verify_password(PASSWORD, 'garbage')
    with pytest.raises(Exception):
        hash_password('short')


def test_login_issues_session_not_machine_credential_and_storage_accepts_it(site):
    data = signin(site, ' ADMIN ')
    assert data['code'] == 'console-admin' and data['tenant_id'] == 'tenant-auth-a'
    token = data['access_token']
    assert token != 'test-legacy-machine'
    assert site['core'].get('/api/v1/auth/me', headers=headers(token)).json()['data']['password_session'] is True
    r = site['storage'].get('/api/v1/storage/volumes', headers=headers(token))
    assert r.status_code == 200
    assert [x['name'] for x in r.json()['data']] == ['Volume A']
    with site['sf']() as db:
        assert db.query(ConsoleSession).one().token_hash == hash_token(token)
        assert db.get(AuthSubject, 'legacy-machine').token_hash == hash_token('test-legacy-machine')
        assert PASSWORD not in db.query(ConsoleAccount).filter_by(username='admin').one().password_hash
        audit = json.dumps([e.new_ref for e in db.query(AuditEvent).all()])
        assert PASSWORD not in audit and token not in audit


@pytest.mark.parametrize('body', [
    {'username': 'admin', 'password': 'incorrect'},
    {'username': 'unknown', 'password': PASSWORD},
    {'username': 'admin', 'password': ['not a string']},
    {'username': 'bad!name', 'password': PASSWORD},
    {'username': 'admin', 'password': 'x'*129},
])
def test_invalid_credentials_do_not_issue_or_echo_credentials(site, body):
    r = site['core'].post('/api/v1/auth/login', json=body)
    assert r.status_code == 401
    assert 'access_token' not in r.text and PASSWORD not in r.text
    with site['sf']() as db:
        assert db.query(ConsoleSession).count() == 0


def test_five_failures_lock_and_expiry_recovers_without_password_reset(site):
    for _ in range(5):
        assert site['core'].post('/api/v1/auth/login', json={'username':'admin','password':'wrong'}).status_code == 401
    assert site['core'].post('/api/v1/auth/login', json={'username':'admin','password':PASSWORD}).status_code == 401
    with site['sf']() as db:
        row = db.query(ConsoleAccount).filter_by(username='admin').one()
        assert row.locked_until > utcnow()
        row.locked_until = utcnow() - timedelta(seconds=1); db.commit()
    assert signin(site)['access_token']


def test_logout_and_expiration_reject_sessions_in_core_and_storage(site):
    first = signin(site)['access_token']
    assert site['core'].post('/api/v1/auth/logout', headers=headers(first)).status_code == 200
    assert site['core'].get('/api/v1/auth/me', headers=headers(first)).status_code == 401
    assert site['storage'].get('/api/v1/storage/volumes', headers=headers(first)).status_code == 401
    second = signin(site)['access_token']
    with site['sf']() as db:
        db.query(ConsoleSession).filter_by(token_hash=hash_token(second)).one().expires_at = utcnow()-timedelta(seconds=1)
        db.commit()
    assert site['core'].get('/api/v1/auth/me', headers=headers(second)).status_code == 401
    assert site['storage'].get('/api/v1/storage/volumes', headers=headers(second)).status_code == 401
    assert site['core'].get('/api/v1/auth/me', headers=headers('test-legacy-machine')).status_code == 200


def test_password_change_revokes_all_browser_sessions_only(site):
    first, second = signin(site)['access_token'], signin(site)['access_token']
    r = site['core'].post('/api/v1/auth/password', headers=headers(first),
                          json={'current_password':PASSWORD,'new_password':NEW_PASSWORD})
    assert r.status_code == 200 and r.json()['data']['login_required']
    for token in (first, second):
        assert site['core'].get('/api/v1/auth/me', headers=headers(token)).status_code == 401
    assert site['core'].post('/api/v1/auth/login', json={'username':'admin','password':PASSWORD}).status_code == 401
    assert signin(site, password=NEW_PASSWORD)['access_token']
    assert site['core'].post('/api/v1/auth/login', json={'token':'test-legacy-machine'}).status_code == 200
    assert site['core'].post('/api/v1/auth/password', headers=headers('test-legacy-machine'),
        json={'current_password':NEW_PASSWORD,'new_password':PASSWORD}).status_code == 403


def test_bad_current_password_cannot_change_or_revoke_existing_session(site):
    token = signin(site)['access_token']
    r=site['core'].post('/api/v1/auth/password', headers=headers(token),
        json={'current_password':'incorrect','new_password':NEW_PASSWORD})
    assert r.status_code == 401
    assert site['core'].get('/api/v1/auth/me', headers=headers(token)).status_code == 200
    assert signin(site)['access_token']


def test_disabled_subject_tenant_and_cross_tenant_binding_are_rechecked(site):
    token = signin(site)['access_token']
    with site['sf']() as db:
        db.get(AuthSubject, site['subject']).status = 'DISABLED'; db.commit()
    assert site['core'].get('/api/v1/auth/me', headers=headers(token)).status_code == 401
    assert site['core'].post('/api/v1/auth/login',json={'username':'admin','password':PASSWORD}).status_code == 401
    with site['sf']() as db:
        db.get(AuthSubject, site['subject']).status = 'ACTIVE'
        db.get(Tenant, 'tenant-auth-a').status = 'DISABLED'; db.commit()
    assert site['core'].get('/api/v1/auth/me', headers=headers(token)).status_code == 401
    with site['sf']() as db:
        db.get(Tenant, 'tenant-auth-a').status = 'ACTIVE'
        db.query(ConsoleSession).filter_by(token_hash=hash_token(token)).one().tenant_id = 'tenant-auth-b'; db.commit()
    assert site['core'].get('/api/v1/auth/me', headers=headers(token)).status_code == 401


def test_sessions_use_current_roles_and_never_grant_other_tenant_access(site):
    data=signin(site, 'auditor-b'); h=headers(data['access_token'])
    r=site['storage'].get('/api/v1/storage/volumes', headers=h)
    assert r.status_code == 200 and [x['name'] for x in r.json()['data']] == ['Volume B']
    assert site['core'].get('/api/v1/tenants', headers=h).status_code == 403
    with site['sf']() as db:
        db.get(AuthSubject,data['subject_id']).roles = ['platform_admin'];db.commit()
    assert site['core'].get('/api/v1/tenants', headers=h).status_code == 200
    with site['sf']() as db:
        db.get(AuthSubject,data['subject_id']).roles = [];db.commit()
    assert site['core'].get('/api/v1/tenants', headers=h).status_code == 403
    assert site['core'].get('/api/v1/auth/me', headers=h).json()['data']['roles'] == []


def test_session_limit_revokes_oldest_without_exposing_machine_tokens(site):
    tokens=[signin(site)['access_token'] for _ in range(6)]
    assert len(set(tokens)) == 6
    assert site['core'].get('/api/v1/auth/me',headers=headers(tokens[0])).status_code == 401
    assert site['core'].get('/api/v1/auth/me',headers=headers(tokens[-1])).status_code == 200


def test_cli_service_requires_explicit_reset_preserves_roles_and_tenant(site):
    token=signin(site)['access_token']
    with site['sf']() as db:
        with pytest.raises(ValueError,match='already exists'):
            provision_account(db,tenant_code='auth-a',username='admin',password=NEW_PASSWORD,
                              display_name='ignored',roles=['auditor'])
    with site['sf']() as db:
        with pytest.raises(ValueError,match='different tenant'):
            provision_account(db,tenant_code='auth-b',username='admin',password=NEW_PASSWORD,
                              display_name='ignored',roles=['platform_admin'],reset=True)
    with site['sf']() as db:
        provision_account(db,tenant_code='auth-a',username='admin',password=NEW_PASSWORD,
                          display_name='ignored',roles=['auditor'],reset=True)
    assert site['core'].get('/api/v1/auth/me',headers=headers(token)).status_code == 401
    assert signin(site,password=NEW_PASSWORD)['roles'] == ['platform_admin']


def migration_cycle(engine):
    run_migrations(engine)
    sf=make_session_factory(engine)
    with sf() as db:
        db.add(Tenant(id='legacy-t',code='legacy',name='Legacy'))
        db.flush()
        db.add(AuthSubject(id='legacy-s',tenant_id='legacy-t',kind='user',subject_code='legacy',
            token_hash=hash_token('preserved'),roles=['operator'],status='ACTIVE'))
        db.commit()
    ConsoleSession.__table__.drop(engine)
    ConsoleAccount.__table__.drop(engine)
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM schema_migrations WHERE step=13'))
    result=run_migrations(engine)
    assert (13,'013_console_password_sessions','APPLIED') in result
    assert inspect(engine).has_table('console_account') and inspect(engine).has_table('console_session')
    assert all(r=='ALREADY_APPLIED' for _,_,r in run_migrations(engine))
    with sf() as db:
        assert db.get(AuthSubject,'legacy-s').token_hash == hash_token('preserved')
        provision_account(db,tenant_code='legacy',username='legacy',password=PASSWORD,
                          display_name='Legacy',roles=['operator'])
        from contracts.passwords import password_login
        assert password_login(db,'legacy',PASSWORD)['access_token']


def test_migration_013_existing_sqlite_database_is_additive(tmp_path):
    engine=make_engine(f'sqlite:///{tmp_path}/m013.sqlite3')
    try:migration_cycle(engine)
    finally:engine.dispose()


def test_migration_013_postgresql_and_real_login():
    # Reuse the existing offline PG fixture, in a dedicated temporary database.
    import shutil,subprocess,time,uuid
    if not shutil.which('docker'):pytest.skip('Docker unavailable')
    name='blyb-auth-pg-'+uuid.uuid4().hex[:8]
    probe=subprocess.run(['docker','version','--format','{{.Server.Version}}'],capture_output=True,timeout=30)
    if probe.returncode:pytest.skip('Docker daemon unavailable')
    subprocess.run(['docker','run','-d','--name',name,'-e','POSTGRES_PASSWORD=synthetic-auth-test',
                    '-e','POSTGRES_USER=blyb','-e','POSTGRES_DB=blyb','-p','127.0.0.1::5432','postgres:16'],check=True,capture_output=True,timeout=90)
    engine=None
    try:
        port=subprocess.check_output(['docker','port',name,'5432/tcp'],text=True).strip().rsplit(':',1)[1]
        engine=make_engine('postgresql+psycopg://blyb:synthetic-auth-test@127.0.0.1:'+port+'/blyb')
        for _ in range(30):
            try:
                with engine.connect() as conn:conn.execute(text('SELECT 1'))
                break
            except Exception:time.sleep(1)
        migration_cycle(engine)
        # PostgreSQL row locks must serialize concurrent failed logins: five
        # simultaneous attempts must trigger the same lock as five sequential ones.
        from concurrent.futures import ThreadPoolExecutor
        from contracts import ApiError
        from contracts.passwords import password_login
        sf=make_session_factory(engine)
        def bad_login(_):
            with sf() as db:
                try:password_login(db,'legacy','incorrect')
                except ApiError as e:return e.code
                raise AssertionError('Incorrect password accepted')
        with ThreadPoolExecutor(max_workers=5) as pool:
            assert list(pool.map(bad_login,range(5)))==['UNAUTHORIZED']*5
        with sf() as db:
            account=db.query(ConsoleAccount).filter_by(username='legacy').one()
            assert account.locked_until>utcnow() and account.failed_attempts==0
    finally:
        if engine is not None:engine.dispose()
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)
