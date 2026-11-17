"""Local-only synthetic browser test server; no production data or connectors."""
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'implementation'))
import _bootstrap
import app_runtime
import uvicorn
from core_db import make_engine,make_session_factory,run_migrations
from core_db.models import Tenant
from contracts.passwords import provision_account
from apps.core_api.app import create_app

parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,required=True);parser.add_argument('--db',required=True)
args=parser.parse_args()
engine=make_engine('sqlite:///'+args.db);run_migrations(engine);sf=make_session_factory(engine)
with sf() as db:
    db.add(Tenant(id='synthetic-browser',code='synthetic',name='Synthetic browser test'));db.commit()
    provision_account(db,tenant_code='synthetic',username='admin',password='Synthetic-browser-pass-123',
                      display_name='Synthetic administrator',roles=['platform_admin'])
app=create_app(session_factory=sf,engine=engine,registry=app_runtime.load_registry(['metrics']))
uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='error')
