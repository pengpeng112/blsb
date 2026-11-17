"""药品追溯目的地真实配置发布工具（FB-01/FB-05；DTR-FIRSTBILL-EXEC-20261021）。

与 API `POST /api/v1/drug-trace/destinations/{id}/config-revisions` 共用
`domains.drug_trace.service.publish_destination_config` 同一校验/发布路径；
本工具不提供任何直接改库状态为 PUBLISHED/APPROVED 的旁路。

用法（<prod-host> 上，经 BLYB_DATABASE_URL 连库；plugins/packages 已在 PYTHONPATH）：

  # 1) check：只读查看当前目的地/配置世代/就绪（零写入）
  python -m apps.publish_drug_trace_config --tenant first-hospital --check

  # 2) dry-run：校验受限输入文件 + 预览将发布的世代（零写入）
  python -m apps.publish_drug_trace_config --tenant first-hospital \
      --destination <dest-id> --config /root/config.real.json --dry-run

  # 3) apply：显式发布（--publish 才写 PUBLISHED；默认仍只建 DRAFT）
  python -m apps.publish_drug_trace_config --tenant first-hospital \
      --destination <dest-id> --config /root/config.real.json \
      --set-real --publish --apply

安全边界：
- --apply 必须显式 --tenant 与 --destination（多目的地不自动选择）。
- 配置文件只含 SecretRef（env:…），绝不含明文 Secret；app_key 可用
  "$ENV:VAR" 占位从环境注入（受限服务环境），不落配置文件。
- 不改字典/证据/发送授权；发布后 readiness 由统一模块动态计算。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _connect():
    url = os.environ.get("BLYB_DATABASE_URL", "")
    if not url:
        print("BLYB_DATABASE_URL 未设置", file=sys.stderr)
        raise SystemExit(2)
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    from core_db import make_engine, make_session_factory
    engine = make_engine()
    sf = make_session_factory(engine)
    return sf, engine


def _tenant(db, code: str):
    from core_db.models import Tenant
    t = db.query(Tenant).filter_by(code=code).first()
    if t is None:
        print(f"租户 {code!r} 不存在", file=sys.stderr)
        raise SystemExit(2)
    return t


def _load_content(path: str) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("config file must contain a JSON object")
    # "$ENV:VAR" 占位：值从受限服务环境注入（如 app_key），不落文件
    def _resolve(v):
        if isinstance(v, str) and v.startswith("$ENV:"):
            env_val = os.environ.get(v[5:], "")
            if not env_val:
                raise SystemExit(f"环境变量 {v[5:]} 未设置（占位 {v}）")
            return env_val
        return v
    return {k: _resolve(v) for k, v in raw.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", required=True, help="租户 code（必填）")
    ap.add_argument("--destination", default="", help="目的地 id（apply 必填）")
    ap.add_argument("--config", help="配置 JSON 文件（受限输入；只含 SecretRef）")
    ap.add_argument("--check", action="store_true", help="只读现状")
    ap.add_argument("--dry-run", action="store_true",
                    help="校验+预览（默认；零写入）")
    ap.add_argument("--apply", action="store_true",
                    help="执行写入（不加 --publish 只建 DRAFT）")
    ap.add_argument("--publish", action="store_true",
                    help="发布 PUBLISHED 并世代切换（RETIRE 旧 PUBLISHED）")
    ap.add_argument("--set-real", action="store_true",
                    help="DRAFT mock 目的地显式切真实（is_mock=False）")
    args = ap.parse_args()

    sf, engine = _connect()
    try:
        with sf() as db:
            t = _tenant(db, args.tenant)
            from core_db.models import ConfigRevision, Destination
            if args.check or not args.config:
                dests = (db.query(Destination)
                         .filter_by(tenant_id=t.id).all())
                out = {"tenant": t.code, "destinations": []}
                for d in dests:
                    cfgs = (db.query(ConfigRevision)
                            .filter_by(tenant_id=t.id,
                                       config_type="connector", owner_id=d.id)
                            .order_by(ConfigRevision.revision).all())
                    out["destinations"].append({
                        "id": d.id, "code": d.code,
                        "profile_revision": d.profile_revision,
                        "status": d.status, "is_mock": bool(d.is_mock),
                        "config_revision": d.config_revision,
                        "revisions": [{"revision": r.revision,
                                       "state": r.state,
                                       "digest": r.digest[:12]}
                                      for r in cfgs]})
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.apply and not args.destination:
                print("--apply 需要显式 --destination（多目的地不自动选择）",
                      file=sys.stderr)
                return 2
            content = _load_content(args.config)
            dest = db.get(Destination, args.destination) \
                if args.destination else None
            if args.destination and (dest is None or dest.tenant_id != t.id):
                print("目的地不存在或不属于该租户", file=sys.stderr)
                return 2
            from domains.drug_trace import service as S
            effective_mock = False if args.set_real else bool(dest.is_mock)
            problems = S.validate_destination_config(
                content, is_mock=effective_mock)
            preview = {
                "tenant": t.code, "destination": args.destination,
                "destination_code": dest.code if dest else None,
                "publish": args.publish, "set_real": args.set_real,
                "problems": problems, "valid": not problems,
                "content_keys": sorted(content.keys()),
            }
            if not args.apply:
                preview["dry_run"] = True
                print(json.dumps(preview, ensure_ascii=False, indent=2))
                return 0 if not problems else 1
            if problems:
                print(json.dumps(preview, ensure_ascii=False, indent=2),
                      file=sys.stderr)
                return 1
            result = S.publish_destination_config(
                db, t.id, args.destination, content,
                publish=args.publish, set_real=args.set_real,
                actor="ops:publish_drug_trace_config")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
