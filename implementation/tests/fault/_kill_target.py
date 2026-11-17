"""W07 kill矩阵子进程靶标。

用法：python tests/fault/_kill_target.py <db_url> <session_id>
环境变量 BLYB_TEST_CRASH_AT=<阶段名> 命中 service 的注入点时 os._exit(137)。
未命中则正常完成 verify_session 并输出 VERIFY_DONE。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401

from core_db import make_engine, make_session_factory  # noqa: E402
from core_db.models import UploadSession  # noqa: E402


def main() -> int:
    db_url, session_id = sys.argv[1], sys.argv[2]
    os.environ["BLYB_DATABASE_URL"] = db_url
    engine = make_engine()
    sf = make_session_factory(engine)
    from apps.storage_service import service
    with sf() as db:
        s = db.get(UploadSession, session_id)
        if s is None:
            print(f"SESSION_NOT_FOUND:{session_id}", flush=True)
            return 2
        service.verify_session(db, s)
    print("VERIFY_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
