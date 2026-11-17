"""Create/reset a console password. Password input is TTY or stdin, never argv."""
from __future__ import annotations

import argparse
import getpass
import json
import sys

from contracts.passwords import provision_account
from core_db import make_engine, make_session_factory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tenant', required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--display-name', default='系统管理员')
    parser.add_argument('--role', action='append', required=True)
    parser.add_argument('--password-stdin', action='store_true')
    parser.add_argument('--reset-password', action='store_true')
    args = parser.parse_args(argv)
    password = sys.stdin.readline().rstrip('\r\n') if args.password_stdin else getpass.getpass('Password: ')
    if not args.password_stdin and password != getpass.getpass('Confirm password: '):
        parser.error('Passwords do not match')
    engine = make_engine()
    try:
        with make_session_factory(engine)() as db:
            result = provision_account(db, tenant_code=args.tenant, username=args.username, password=password,
                display_name=args.display_name, roles=args.role, reset=args.reset_password)
            print(json.dumps(result, ensure_ascii=False))
    finally:
        engine.dispose()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
