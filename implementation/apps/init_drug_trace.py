"""药品追溯幂等初始化/检查/就绪报告工具（DTR-FINALIZE-20261020 FIN-06）。

用法（在 <prod-host> 生产/隔离环境，经 BLYB_DATABASE_URL 连库）：

  python -m apps.init_drug_trace --check          # 只读：现状+缺口清单
  python -m apps.init_drug_trace                  # dry-run（默认）：预览草稿差异
  python -m apps.init_drug_trace --apply          # 写入草稿（幂等；不发布）
  python -m apps.init_drug_trace --readiness      # 分层就绪报告
  可选 --tenant <code>：多租户时**必须**显式指定；未指定仅当唯一租户时继续
  （DPL-P05：不静默落到第一个租户）。

安全边界（不可绕过）：
- 只创建 **DRAFT** 连接器/目的地/字典草稿与 ConfigRevision(DRAFT)；
  不写 APPROVED 生产证据、不发布字典（PUBLISH 由平台管理员走既有 API）、
  不生成任意租户通配 scope、不启动采集/发送任务。
- 幂等：按 (tenant, code/plugin/profile) 精确查重；重复执行不重复建对象、
  不轮换令牌、不覆盖既有字典/历史提交。
- readiness 分层：CODE_READY / LOCAL_SERVICE_READY / NETWORK_READY /
  SOURCE_READY / PLATFORM_READY / SEND_SCOPE_READY；未测=UNKNOWN，模板
  齐全不等于 READY。不绕过 /api/v1/drug-trace/readiness 的证据门禁。

真实密钥注入提醒：worker 侧 AppSecret 仅经 env: SecretRef（受限服务环境，
compose 覆盖文件注入）；本工具不接触密钥值。file: SecretRef 未实现。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone


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


PROFILE_RETAIL = "alihealth_yljg_retail_v1"

# 字典草稿清单（医院/药房映射；发布走平台管理员 API）
DICT_DRAFTS = {
    "drugtrace.ref_ent_id": {
        "note": "药房 → 医院平台 ref_ent_id（已保存在本机 profile，不入库明文）",
        "items_hint": "每药房一行：{code: <pharmacy_code>, label: <药房名>, "
                      "target: <ref_ent_id>}",
    },
    "drugtrace.query_bill_type": {
        "note": "业务分类 → 查询选择器（A/AI/AO；官方当期口径 EXT-09）",
        "items_hint": "PATIENT_DISPENSE→A（示例；以批准字典为准）",
    },
    "drugtrace.bill_type": {
        "note": "业务分类 → 上传 bill_type（321/323 待平台确认 EXT-08；"
                "未批准不预置默认值）",
        "items_hint": "留空待批准；不猜值",
    },
}


def _tenant(db, code: str | None = None):
    """DPL-P05：显式租户选择。--tenant CODE 精确匹配；未指定时仅当**唯一**
    租户才继续（多租户必须显式选择，绝不静默落到第一个）。"""
    from core_db.models import Tenant
    if code:
        t = db.query(Tenant).filter_by(code=code).first()
        if t is None:
            codes = [x.code for x in db.query(Tenant).all()]
            print(f"租户 {code!r} 不存在；现有：{codes}", file=sys.stderr)
            raise SystemExit(2)
        return t
    all_t = db.query(Tenant).order_by(Tenant.created_at).all()
    if len(all_t) > 1:
        print(f"存在 {len(all_t)} 个租户，必须用 --tenant 显式指定："
              f"{[x.code for x in all_t]}（不默认取第一个）", file=sys.stderr)
        raise SystemExit(2)
    if not all_t:
        return None
    return all_t[0]


def _now() -> str:  # 兼容保留：历史版本用于就绪报告时间戳（现由 readiness 模块生成）
    return datetime.now(timezone.utc).isoformat()


def check_state(db, tenant_id: str) -> dict:
    """只读现状：连接器/目的地/配置/字典/证据各多少、什么状态。"""
    from core_db.models import (ConfigRevision, ConnectorInstance,
                                Destination, DictionaryRevision)
    conns = db.query(ConnectorInstance).filter_by(
        tenant_id=tenant_id,
        plugin_id="sources.db_view.drug_trace").all()
    dests = db.query(Destination).filter_by(
        tenant_id=tenant_id, profile_revision=f"{PROFILE_RETAIL}@1").all()
    # FB-01：目的地配置世代统一口径 config_type="connector"（owner=目的地 id，
    # 与域服务 _load_config_revision/测试一致；旧 "destination" 查询从未命中）
    cfgs = db.query(ConfigRevision).filter_by(
        tenant_id=tenant_id, config_type="connector").all()
    dicts = {d.namespace: d.version for d in db.query(DictionaryRevision)
             .filter_by(tenant_id=tenant_id).all()
             if d.namespace in DICT_DRAFTS}
    try:
        from domains.drug_trace.models import DrugTraceProductionEvidence
        evid = db.query(DrugTraceProductionEvidence).filter_by(
            tenant_id=tenant_id).count()
    except Exception:  # noqa: BLE001 —— 插件未加载时表可能不存在
        evid = None
    return {
        "connectors": [{"id": c.id, "status": c.status,
                        "active_config": bool(c.active_config_revision)}
                       for c in conns],
        "destinations": [{"id": d.id, "code": d.code, "is_mock": d.is_mock,
                          "status": d.status,
                          "config_revision": d.config_revision}
                         for d in dests],
        "config_revisions": [{"id": c.id, "state": c.state,
                              "owner": c.owner_id, "revision": c.revision}
                             for c in cfgs],
        "dictionary_drafts_present": dicts,
        "dictionary_drafts_missing": [ns for ns in DICT_DRAFTS
                                      if ns not in dicts],
        "production_evidence_count": evid,
    }


def plan_drafts(db, tenant_id: str) -> dict:
    """计算将创建的草稿（不写入）：幂等查重后仅列缺口。"""
    from core_db.models import ConnectorInstance, Destination
    actions = []
    conn = db.query(ConnectorInstance).filter_by(
        tenant_id=tenant_id,
        plugin_id="sources.db_view.drug_trace").first()
    if conn is None:
        actions.append({"kind": "connector", "why": "药品追溯 HIS 只读连接器",
                        "draft": {"direction": "source",
                                  "plugin_id": "sources.db_view.drug_trace",
                                  "name": "his-drugtrace",
                                  "status": "DRAFT"}})
    dest = db.query(Destination).filter_by(
        tenant_id=tenant_id, code="alihealth-drugtrace-retail").first()
    if dest is None:
        actions.append({"kind": "destination", "why": "码上放心零售目的地（草稿）",
                        "draft": {"code": "alihealth-drugtrace-retail",
                                  "name": "码上放心-患者发药（uploadretail）",
                                  "profile_revision": f"{PROFILE_RETAIL}@1",
                                  "mode": "PUSH",
                                  "capabilities": ["upload", "query"],
                                  "is_mock": True,   # 草稿默认 mock；切真实走配置换代
                                  "status": "DRAFT"}})
    actions.append({"kind": "config-draft",
                    "why": "destination ConfigRevision 草稿（dmz_base_url/"
                           "SecretRef env:/mTLS 证书路径待受控填写）",
                    "draft": {
                        "config_type": "destination",
                        "content_schema": {
                            "app_key": "str（本机 profile 已有）",
                            "app_secret_ref": "env:BLYB_ALIHEALTH_APP_SECRET",
                            "dmz_base_url": "https://<relay-net-host>",
                            "dmz_ca_file": "/etc/blyb/certs/dmz-ca.crt",
                            "dmz_client_cert_file": "/etc/blyb/certs/client.crt",
                            "dmz_client_key_file": "/etc/blyb/certs/client.key",
                            "session_ref": "env:BLYB_ALIHEALTH_SESSION（可选）",
                            "query_date_semantics": "both（官方确认后改窄）",
                        }}})
    for ns, spec in DICT_DRAFTS.items():
        actions.append({"kind": "dictionary-draft", "why": spec["note"],
                        "draft": {"namespace": ns, "version": "draft-1",
                                  "items": [], "source": "target",
                                  "hint": spec["items_hint"]}})
    return {"actions": actions,
            "material_checklist": [
                {"item": "DMZ 转发服务 LOCAL_READY", "cmd":
                 "见 deploy/dmz install.sh --verify（DMZ 本机）"},
                {"item": "正式 TLS/mTLS 证书（院方 PKI 或 TEST-ONLY 隔离）"},
                {"item": "HIS readonly 绑定 + r7 视图核验（EXT-01/02）"},
                {"item": "平台权限/字典口径 321/323/查询日期（EXT-06/08/09）"},
                {"item": "签名资格验证证据（EXT-07）"},
                {"item": "首批发送 scope（机构/药房/窗口/max_bills，EXT-12）"},
            ]}


def apply_drafts(db, tenant_id: str, plan: dict) -> list[str]:
    """写入草稿（幂等）。返回实际创建的对象描述。"""
    from contracts import new_uuid
    from core_db.models import (ConnectorInstance, Destination,
                                DictionaryRevision)
    created = []
    for act in plan["actions"]:
        d = act["draft"]
        if act["kind"] == "connector":
            db.add(ConnectorInstance(id=new_uuid(), tenant_id=tenant_id,
                                     **d))
            created.append("connector:his-drugtrace(DRAFT)")
        elif act["kind"] == "destination":
            db.add(Destination(id=new_uuid(), tenant_id=tenant_id, **d))
            created.append("destination:alihealth-drugtrace-retail(DRAFT,mock)")
        elif act["kind"] == "dictionary-draft":
            ns = d["namespace"]
            exists = db.query(DictionaryRevision).filter_by(
                tenant_id=tenant_id, namespace=ns,
                version=d["version"]).first()
            if exists is None:
                dd = dict(d)
                dd.pop("hint", None)
                db.add(DictionaryRevision(id=new_uuid(),
                                          tenant_id=tenant_id, **dd))
                created.append(f"dictionary:{ns}(draft-1, 未发布)")
        # config-draft：schema 展示不写库（真实配置经管理 API 创建/发布）
    db.commit()
    return created


def readiness_report(db, tenant_id: str, *, profile: str = PROFILE_RETAIL,
                     destination_id: str = "",
                     config_release_id: str | None = None) -> dict:
    """分层就绪（FB-01）：委托 domains.drug_trace.readiness.layer_report——
    与 /api/v1/drug-trace/readiness 同一实现，CLI/API/UI 不再各维护一套。

    动态口径：SOURCE/PLATFORM/SEND_SCOPE 由当前证据/字典/签名实际状态计算；
    Mock/DRAFT、缺 active config、证据缺项/过期/撤销、配置换代如实显示。
    NETWORK 恒 UNKNOWN（本工具不做网络探测；外部实测见任务 evidence）。
    """
    from domains.drug_trace import readiness as R
    return R.layer_report(db, tenant_id, profile=profile,
                          destination_id=destination_id,
                          config_release_id=config_release_id)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="只读现状+缺口")
    ap.add_argument("--apply", action="store_true",
                    help="写入草稿（默认 dry-run 只预览）")
    ap.add_argument("--readiness", action="store_true", help="分层就绪报告")
    ap.add_argument("--tenant", default=None,
                    help="目标租户 code（多租户时必填；单租户可省略，"
                         "输出中回显实际租户）")
    ap.add_argument("--profile", default=PROFILE_RETAIL,
                    help=f"就绪报告目标 profile（默认 {PROFILE_RETAIL}）")
    ap.add_argument("--destination", default="",
                    help="显式目的地 id（同 profile 多目的地时必填，"
                         "不自动取第一个）")
    ap.add_argument("--config-release", default=None,
                    help="按该配置代次核对 send 证据闭包（可选）")
    args = ap.parse_args()
    sf, engine = _connect()
    try:
        with sf() as db:
            t = _tenant(db, args.tenant)
            if t is None:
                print("无租户（先 bootstrap）", file=sys.stderr)
                return 2
            print(f"[tenant] code={t.code} id={t.id}"
                  f"（--tenant 未指定时仅单租户自动选择）",
                  file=sys.stderr)
            if args.readiness:
                report = readiness_report(
                    db, t.id, profile=args.profile,
                    destination_id=args.destination,
                    config_release_id=args.config_release)
                print(json.dumps(report, ensure_ascii=False, indent=2))
                if report.get("destination_ambiguous"):
                    print(f"注意：profile {args.profile} 存在多个目的地"
                          f"{report.get('destination_candidates')}；"
                          "必须用 --destination 显式指定", file=sys.stderr)
                    return 2
                return 0
            state = check_state(db, t.id)
            plan = plan_drafts(db, t.id)
            if args.check:
                print(json.dumps({"state": state, "plan": plan},
                                 ensure_ascii=False, indent=2, default=str))
                return 0
            if not args.apply:
                print(json.dumps({"dry_run": True, "plan": plan},
                                 ensure_ascii=False, indent=2, default=str))
                print("（dry-run：加 --apply 写入草稿；不发布、不批证据）")
                return 0
            created = apply_drafts(db, t.id, plan)
            print(json.dumps({"applied": created,
                              "not_created_again":
                                  [a["kind"] for a in plan["actions"]
                                   if a["kind"] == "config-draft"]},
                             ensure_ascii=False, indent=2))
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
