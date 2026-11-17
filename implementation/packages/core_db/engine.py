"""数据库引擎与会话工厂。

默认本地开发/测试使用 SQLite(WAL)；生产通过 BLYB_DATABASE_URL 指向 PostgreSQL。
时间统一以 UTC 朴素值保存（SQLite 不保留时区信息），API 出入口转换为 RFC3339。
"""
from __future__ import annotations

import os
from datetime import date, datetime, time, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# RT-02：Python 3.12+ 弃用 sqlite3 默认 datetime/date/time 适配器（raw
# text() SQL 绑定时间参数时 DeprecationWarning 刷屏数千条）。按官方 recipe
# 显式注册**等价格式**适配器——输出与旧默认逐字节一致（isoformat(" ") /
# isoformat()），在库数据零变化；SQLAlchemy 类型化列本就经自身 bind 处理器，
# 不经过这里。
import sqlite3 as _sqlite3

_sqlite3.register_adapter(datetime, lambda v: v.isoformat(" "))
_sqlite3.register_adapter(date, lambda v: v.isoformat())
_sqlite3.register_adapter(time, lambda v: v.isoformat())

_DEFAULT_SQLITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "var", "blyb.sqlite3")


def database_url() -> str:
    url = os.environ.get("BLYB_DATABASE_URL", "")
    if url:
        return url
    os.makedirs(os.path.dirname(os.path.abspath(_DEFAULT_SQLITE)), exist_ok=True)
    return f"sqlite:///{os.path.abspath(_DEFAULT_SQLITE)}"


def utcnow() -> datetime:
    """UTC 朴素时间：所有表列统一使用本函数。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None, *, echo: bool = False):
    url = url or database_url()
    if url.startswith("sqlite"):
        eng = create_engine(url, echo=echo, connect_args={"check_same_thread": False})
        _enable_sqlite_pragmas(eng)
        return eng
    return create_engine(url, echo=echo, pool_pre_ping=True)


def _enable_sqlite_pragmas(eng) -> None:
    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, _record):  # pragma: no cover - 环境相关
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=FULL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
