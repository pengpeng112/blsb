"""开发/演示环境种子：租户、主体、来源、字典、HospitalPack、工作流、目的地、阻断。

全部为合成数据；令牌仅用于本地开发，生产由部署配置注入。
"""
from __future__ import annotations

import os

from contracts import new_uuid
from contracts.api import digest_of, hash_token
from core_db.engine import make_engine, make_session_factory, utcnow
from core_db.models import (Agent, AssetLocation, AuthSubject, ConfigRevision,
                            ConnectorInstance, Destination, DictionaryRevision,
                            ExternalBlocker, SourceCheckpoint, SourceSystem,
                            StorageVolume, WorkflowRevision)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEV_SUBJECTS = [
    ("admin", "user", "dev-admin-token", ["platform_admin"], "平台管理员"),
    ("configer", "user", "dev-configer-token", ["config_editor"], "实施配置员"),
    ("reviewer", "user", "dev-reviewer-token", ["business_reviewer"], "业务复核员"),
    ("operator", "user", "dev-operator-token", ["operator"], "运维人员"),
    ("auditor", "user", "dev-auditor-token", ["auditor"], "审计只读"),
    ("collector", "service", "dev-collector-token", ["service"], "交换采集器"),
]

APPROVED_DICTIONARIES = [
    ("id_type_ga", "demo-v1", "target", [
        {"code": "01", "target": "111", "label": "居民身份证"},
        {"code": "02", "target": "112", "label": "居民户口簿"}]),
    # R29 更正：正式S03第三部分 门诊=1、住院=2（慢特病=3待B05门诊路径确认后增补）。
    # demo-v1（住院=1/门诊=2）为历史错误版本：保留行不删改，仅不再被引用。
    ("encounter_kind", "demo-v2", "target", [
        {"code": "住院", "target": "2", "label": "住院"},
        {"code": "门诊", "target": "1", "label": "门诊"}]),
    ("report_org_insurance_code", "demo-v1", "target", [
        {"code": "YLV21051", "target": "H1234567890X",
         "label": "演示报告机构→12位医保码(合成)"}]),
    ("pathology_category", "demo-v1", "target", [
        {"code": "常规病理", "target": "1", "label": "常规病理"}]),
]

HOSPITAL_PACK_APPROVED = {
    "pack_key": "first-hospital-demo-approved",
    "production_enabled": False,
    "evidence_status": "SYNTHETIC_DEMO",
    "sources": {
        "pis": {"plugin": "pis.pitaya.sqlserver", "host": "pis.example.invalid",
                "secret_ref": "UNBOUND", "database_candidate": "PITAYA",
                "database_approved": False, "other_database_candidate": "QPIS",
                "schema": "dbo", "barcode_field": "qptmh",
                "stable_keys": {"slide": ["Id"], "case": None},
                "key_status": "VERIFY_BEFORE_PRODUCTION"},
        "his": {"plugin": "his.medrec.oracle", "host": "his.example.invalid",
                "secret_ref": "UNBOUND", "service_name": None, "schema": "MEDREC",
                "objects": ["PAT_MASTER_INDEX", "PAT_VISIT"],
                "visit_key": ["PATIENT_ID", "VISIT_ID"], "pis_visit_bridge": None}},
    "identity_policy": {"checksum_only_auto_assign_type": False, "fallback_enabled": True,
                        "fallback_rule": "pis_checksum_valid_ga111",
                        "fallback_evidence": "SYNTHETIC_DEMO_APPROVED",
                        "conflict_action": "WAITING_HUMAN"},
    "approved_dictionaries": {
        "id_type_ga": "demo-v1", "encounter_kind": "demo-v2",
        "report_org_insurance_code": "demo-v1", "pathology_category": "demo-v1"},
    # 演示批准字段：申请日期权威字段=集成申请表申请日期；齐套闭合信号批准（合成规则）
    "clinical_policy": {
        "application_time_authoritative_field": "患者检查申请信息表.申请日期",
        "expected_slide_closed_signal_approved": True,
        "missing_required_action": "WAIT"},
    # 经批准的PIS住院号→VISIT_ID桥接（合成演示；正式桥接须B05证据）
    "pis_visit_bridge": {"visit_id_for": {"IN2026001": "1", "IN2026002": "1",
                                          "IN2026004": "1", "IN2026005": "1"},
                         "evidence": "SYNTHETIC_DEMO_APPROVED"},
    "collection_policy": {"created_time_scope": "INSERT_DISCOVERY_ONLY",
                          "verified_update_field": None,
                          "partition_reconciliation_required": True,
                          "automatic_missing_row_tombstone": False},
}

HOSPITAL_PACK_REVIEW_DRAFT = {
    "pack_key": "first-hospital-review-draft",
    "production_enabled": False,
    "evidence_status": "SECOND_PARTY_REPORT_NOT_INDEPENDENTLY_VERIFIED",
    "sources": HOSPITAL_PACK_APPROVED["sources"],
    "identity_policy": {"checksum_only_auto_assign_type": False,
                        "fallback_enabled": False,
                        "approved_dictionary_ref": None,
                        "conflict_action": "WAITING_HUMAN"},
    "clinical_policy": {"application_time_authoritative_field": None,
                        "application_category_dictionary": None,
                        "clinical_status_dictionary": None,
                        "report_organization_mapping": None,
                        "missing_required_action": "WAIT"},
    "collection_policy": HOSPITAL_PACK_APPROVED["collection_policy"],
}

BLOCKERS = [
    ("B01", "PIS数据库 PITAYA/QPIS、表授权、SQL版本未确认", "PIS厂商/DBA",
     "首院生产连接；不阻断Mock与驱动实现"),
    ("B02", "条码内容/大小写/前导零/复用范围未确认", "PIS/扫描厂商",
     "无歧义自动绑定与真机采集"),
    ("B03", "申请时间权威字段及业务定义未确认", "PIS", "正式索引段6"),
    ("B04", "申请类别、状态、齐套、补片与撤销规则未确认", "PIS/病理科",
     "临床自动就绪与索引段8"),
    ("B05", "HIS证件字典、门诊/慢特病就诊路径未确认", "HIS", "身份/就诊自动关联"),
    ("B06", "各报告机构有效医保代码未取得", "医保/院方", "正式索引段7"),
    ("B07", "更新、物理删除、报告版本和父子键未确认", "PIS/DBA", "宣称增量无漏数及自动更正"),
    ("B08", "扫描文件格式、目录、scan_id/revision、完成信号未答复", "扫描厂商",
     "生产扫描自动完成判断"),
    ("B09", "正式病理医保协议、鉴权、更正、回执未发布", "医保", "真实医保生产发送"),
]


def _node(nid, ntype, plugin_key, params=None):
    return {"id": nid, "type": ntype, "plugin_ref": {"key": plugin_key, "version": "1.0"},
            "parameters": params or {}, "timeout_seconds": 300,
            "retry": {"max_attempts": 3, "initial_delay_seconds": 1,
                      "max_delay_seconds": 30}}


def pathology_push_workflow() -> dict:
    return {
        "workflow_key": "pathology.mock_push", "revision": 1,
        "trigger": "acquisition_files_complete", "production_enabled": False,
        "nodes": [
            _node("resolve", "resolve", "pathology.resolve", {"strategy": "pis_barcode_lookup"}),
            _node("quality", "validate", "pathology.quality", {"ruleset_ref": "hospital-demo-quality-v1"}),
            _node("ready", "wait_dependencies", "pathology.completeness", {"policy": "whole_case"}),
            _node("freeze", "package.freeze", "core.package"),
            _node("map", "target.map", "profile.pathology_internal_v1"),
            _node("validate", "target.validate", "core.schema"),
            _node("send", "deliver.push", "destination.mock_http",
                  {"destination_ref": "mock-only"}),
            _node("receipt", "receipt.wait", "destination.mock_http"),
        ],
        "edges": [
            {"from": "resolve", "to": "quality", "on": "success"},
            {"from": "quality", "to": "ready", "on": "success"},
            {"from": "ready", "to": "freeze", "on": "success"},
            {"from": "freeze", "to": "map", "on": "success"},
            {"from": "map", "to": "validate", "on": "success"},
            {"from": "validate", "to": "send", "on": "success"},
            {"from": "send", "to": "receipt", "on": "success"},
        ],
    }


def pathology_publish_workflow() -> dict:
    return {
        "workflow_key": "pathology.mock_publish", "revision": 1,
        "trigger": "package_frozen", "production_enabled": False,
        "nodes": [
            _node("freeze", "package.freeze", "core.package"),
            _node("publish", "deliver.publish", "destination.view_publication"),
        ],
        "edges": [{"from": "freeze", "to": "publish", "on": "success"}],
    }


def metrics_push_workflow() -> dict:
    return {
        "workflow_key": "metrics.mock_push", "revision": 1,
        "trigger": "package_changed", "production_enabled": False,
        "nodes": [
            _node("freeze", "package.freeze", "metrics.package"),
            _node("map", "target.map", "profile.metrics_json"),
            _node("validate", "target.validate", "core.schema"),
            _node("send", "deliver.push", "destination.mock_http"),
            _node("receipt", "receipt.wait", "destination.mock_http"),
        ],
        "edges": [
            {"from": "freeze", "to": "map", "on": "success"},
            {"from": "map", "to": "validate", "on": "success"},
            {"from": "validate", "to": "send", "on": "success"},
            {"from": "send", "to": "receipt", "on": "success"},
        ],
    }


def resolve_volume_root(default_name: str) -> str:
    """对象卷根。BLYB_VOLUME_ROOT 仅供全量 seed；未设置时仍是 implementation/var/<name>。"""
    override = os.environ.get("BLYB_VOLUME_ROOT", "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.join(HERE, "var", default_name)


def _place_primary_volume(db, tenant_id: str, vol_root: str) -> None:
    """登记或对齐 vol1。无对象位置时允许改根；已有位置时拒绝，避免库和文件分家。"""
    from storage_backend import VolumeHandle

    desired = os.path.abspath(vol_root)
    os.makedirs(desired, exist_ok=True)
    existing = db.query(StorageVolume).filter_by(tenant_id=tenant_id, name="vol1").first()
    if existing is None:
        vol_id = new_uuid()
        db.add(StorageVolume(id=vol_id, tenant_id=tenant_id, backend="filesystem",
                             name="vol1", root_ref=desired,
                             safety_margin_bytes=512 * 1024 * 1024,
                             state="ACTIVE", health="OK"))
        db.flush()
        handle = VolumeHandle(vol_id, desired, 0)
        handle.ensure_layout()
        handle.ensure_sentinel()
        return
    current = os.path.abspath(existing.root_ref) if existing.root_ref else ""
    if current != desired:
        used = db.query(AssetLocation).filter_by(volume_id=existing.id).count()
        if used:
            raise RuntimeError(
                f"storage volume vol1 has {used} locations at {current}; "
                f"refusing to switch root to {desired}. "
                "Follow the new-root restore runbook before changing BLYB_VOLUME_ROOT.")
        old_sentinel = os.path.join(current, f".blyb-vol-{existing.id}")
        if os.path.isfile(old_sentinel):
            os.remove(old_sentinel)
        existing.root_ref = desired
        db.flush()
    handle = VolumeHandle(existing.id, desired, existing.safety_margin_bytes or 0)
    handle.ensure_layout()
    handle.ensure_sentinel()


def _dev_subject_codes() -> list[str]:
    """dev-* 演示主体清单（含演示Agent主体）：BLYB_DISABLE_DEV_TOKENS 禁用对象。"""
    return [code for code, *_ in DEV_SUBJECTS] + ["agent:SCAN-WS-01"]


def _apply_token_hardening(db, tenant_id: str) -> dict:
    """T14 令牌加固（幂等；两者都未设置时零动作，默认与现状完全一致）：

    - BLYB_DISABLE_DEV_TOKENS=1：不创建（调用方跳过）且把库中既有 dev-* 演示
      主体置 status='DISABLED'（登录只认 ACTIVE，旧令牌随即401）；
    - BLYB_BOOTSTRAP_ADMIN_TOKEN=<高熵随机>：创建/轮换 platform_admin 引导主体
      （token 哈希存储，同现有 hash_token 机制；重复运行=令牌轮换）。
    """
    info: dict = {"dev_disabled": 0, "bootstrap_admin": None}
    if os.environ.get("BLYB_DISABLE_DEV_TOKENS", "") == "1":
        for code in _dev_subject_codes():
            sub = (db.query(AuthSubject)
                   .filter_by(tenant_id=tenant_id, subject_code=code).first())
            if sub is not None and sub.status != "DISABLED":
                sub.status = "DISABLED"
                info["dev_disabled"] += 1
    token = os.environ.get("BLYB_BOOTSTRAP_ADMIN_TOKEN", "")
    if token:
        sub = (db.query(AuthSubject)
               .filter_by(tenant_id=tenant_id,
                          subject_code="bootstrap-admin").first())
        if sub is None:
            sub = AuthSubject(id=new_uuid(), tenant_id=tenant_id, kind="user",
                              subject_code="bootstrap-admin",
                              display_name="引导管理员（环境令牌）",
                              roles=["platform_admin"])
            db.add(sub)
        sub.token_hash = hash_token(token)
        sub.status = "ACTIVE"
        info["bootstrap_admin"] = sub.id
    return info


def seed(engine=None, session_factory=None, *, with_volume: bool = True) -> dict:
    import _bootstrap  # noqa: F401
    engine = engine or make_engine()
    sf = session_factory or make_session_factory(engine)
    created: dict[str, str] = {}
    # T14 令牌加固：禁用模式下不创建 dev-* 演示主体（既有库中的由
    # _apply_token_hardening 统一禁用）。
    disable_dev = os.environ.get("BLYB_DISABLE_DEV_TOKENS", "") == "1"
    with sf() as db:
        # 租户
        tenant = db.query(AuthSubject).filter_by(subject_code="admin").first()
        from core_db.models import Tenant
        t = db.query(Tenant).filter_by(code="first-hospital").first()
        if t is None:
            t = Tenant(id=new_uuid(), code="first-hospital", name="首院（合成演示）",
                       timezone="Asia/Shanghai")
            db.add(t)
            db.flush()
        created["tenant_id"] = t.id

        # 主体与令牌
        for code, kind, token, roles, name in DEV_SUBJECTS:
            if disable_dev:
                continue  # T14：禁用模式不创建 dev-* 演示主体
            if db.query(AuthSubject).filter_by(tenant_id=t.id, subject_code=code).first():
                continue
            db.add(AuthSubject(id=new_uuid(), tenant_id=t.id, kind=kind,
                               subject_code=code, display_name=name,
                               token_hash=hash_token(token), roles=roles))
        db.flush()

        # 来源系统
        srcs = {}
        for code, kind, ns in (("scanner-simulator", "scanner", "device:scanner"),
                               ("pis", "database", "pis.pitaya.sqlserver@PITAYA.dbo"),
                               ("his", "database", "his.medrec.oracle@MEDREC"),
                               ("metrics-csv", "csv_import", "import:metrics")):
            s = db.query(SourceSystem).filter_by(tenant_id=t.id, code=code).first()
            if s is None:
                s = SourceSystem(id=new_uuid(), tenant_id=t.id, code=code, kind=kind,
                                 identifier_namespace=ns)
                db.add(s)
                db.flush()
            srcs[code] = s.id

        # 设备 + agent 主体
        agent = db.query(Agent).filter_by(tenant_id=t.id, device_code="SCAN-WS-01").first()
        if agent is None:
            agent = Agent(id=new_uuid(), tenant_id=t.id,
                          source_system_id=srcs["scanner-simulator"],
                          device_code="SCAN-WS-01", device_name="演示扫描工作站",
                          status="ACTIVE")
            db.add(agent)
            db.flush()
        if not db.query(AuthSubject).filter_by(tenant_id=t.id,
                                               subject_code="agent:SCAN-WS-01").first():
            if not disable_dev:  # T14：禁用模式不创建演示Agent令牌
                db.add(AuthSubject(id=new_uuid(), tenant_id=t.id, kind="agent",
                                   subject_code="agent:SCAN-WS-01",
                                   display_name="Agent SCAN-WS-01",
                                   token_hash=hash_token("dev-agent-token"),
                                   roles=["service"], agent_id=agent.id))
        created["agent_id"] = agent.id

        # 存储卷。内网启动设置 BLYB_VOLUME_ROOT=var/intranet/volume1，
        # 避免和开发栈共用 implementation/var/volume1。
        if with_volume:
            _place_primary_volume(db, t.id, resolve_volume_root("volume1"))

        # 连接器实例 + checkpoint
        for code, plugin, direction in (("pis-conn", "pis.pitaya.sqlserver", "source"),
                                        ("his-conn", "his.medrec.oracle", "source")):
            ci = (db.query(ConnectorInstance)
                  .filter_by(tenant_id=t.id, plugin_id=plugin).first())
            if ci is None:
                ci = ConnectorInstance(id=new_uuid(), tenant_id=t.id, direction=direction,
                                       plugin_id=plugin, name=code,
                                       source_system_id=srcs.get("pis" if "pis" in plugin else "his"),
                                       status="ENABLED")
                db.add(ci)
                db.flush()
            if code == "pis-conn" and not db.query(SourceCheckpoint).filter_by(
                    tenant_id=t.id, connector_id=ci.id, stream="pis.created").first():
                db.add(SourceCheckpoint(id=new_uuid(), tenant_id=t.id, connector_id=ci.id,
                                        stream="pis.created", partition="0",
                                        cursor_json={"since": "0000-01-01T00:00:00"}))
        db.flush()

        # 字典（演示批准版；均标 source=target用途）
        for ns, ver, src, items in APPROVED_DICTIONARIES:
            if not db.query(DictionaryRevision).filter_by(
                    tenant_id=t.id, namespace=ns, version=ver).first():
                db.add(DictionaryRevision(id=new_uuid(), tenant_id=t.id, namespace=ns,
                                          version=ver, items=items, source=src,
                                          approved_by="demo-seed",
                                          effective_from=utcnow()))

        # HospitalPack：批准演示版发布为当前；复核草案留草稿
        if not db.query(ConfigRevision).filter_by(
                tenant_id=t.id, config_type="hospital_pack").first():
            db.add(ConfigRevision(
                id=new_uuid(), tenant_id=t.id, config_type="hospital_pack",
                owner_id="first-hospital", revision=1,
                content_json=HOSPITAL_PACK_REVIEW_DRAFT,
                digest=digest_of(HOSPITAL_PACK_REVIEW_DRAFT), state="DRAFT",
                created_by="seed"))
            db.add(ConfigRevision(
                id=new_uuid(), tenant_id=t.id, config_type="hospital_pack",
                owner_id="first-hospital", revision=2,
                content_json=HOSPITAL_PACK_APPROVED,
                digest=digest_of(HOSPITAL_PACK_APPROVED), state="PUBLISHED",
                created_by="seed"))
        else:
            # R29 历史影响处理：已发布 pack 若仍引用错误编码（encounter_kind
            # demo-v1 住院=1/门诊=2），生成更正修订（引用demo-v2）并发布；
            # 旧revision退役不删除，历史冻结包不受影响。
            published = (db.query(ConfigRevision)
                         .filter_by(tenant_id=t.id, config_type="hospital_pack",
                                    state="PUBLISHED")
                         .order_by(ConfigRevision.revision.desc()).first())
            if published is not None and \
                    (published.content_json or {}).get(
                        "approved_dictionaries", {}).get("encounter_kind") == "demo-v1":
                corrected = dict(published.content_json)
                refs = dict(corrected.get("approved_dictionaries") or {})
                refs["encounter_kind"] = "demo-v2"
                corrected["approved_dictionaries"] = refs
                corrected["correction_note"] = (
                    "encounter_kind coding corrected to S03 (门诊=1/住院=2) "
                    f"superseding revision {published.revision}")
                db.add(ConfigRevision(
                    id=new_uuid(), tenant_id=t.id, config_type="hospital_pack",
                    owner_id="first-hospital",
                    revision=published.revision + 1,
                    content_json=corrected, digest=digest_of(corrected),
                    state="PUBLISHED", created_by="seed:correction"))
                published.state = "RETIRED"

        # 目的地
        if not db.query(Destination).filter_by(tenant_id=t.id, code="mock-push").first():
            db.add(Destination(id=new_uuid(), tenant_id=t.id, code="mock-push",
                               name="医保Mock推送（非真实平台）", profile_revision="destination.mock_http@1.0",
                               mode="PUSH", capabilities=["submit", "query", "receipt_poll"],
                               is_mock=True))
        if not db.query(Destination).filter_by(tenant_id=t.id, code="first-hospital-exchange").first():
            db.add(Destination(id=new_uuid(), tenant_id=t.id, code="first-hospital-exchange",
                               name="交换发布视图（PULL）", profile_revision="destination.view_publication@1.0",
                               mode="PULL_PUBLICATION", capabilities=["view", "file_download"],
                               is_mock=True))

        # 工作流
        for wf_factory in (pathology_push_workflow, pathology_publish_workflow,
                           metrics_push_workflow):
            dsl = wf_factory()
            if not db.query(WorkflowRevision).filter_by(
                    tenant_id=t.id, workflow_key=dsl["workflow_key"],
                    revision=dsl["revision"]).first():
                db.add(WorkflowRevision(
                    id=new_uuid(), tenant_id=t.id, workflow_key=dsl["workflow_key"],
                    revision=dsl["revision"], dsl=dsl, digest=digest_of(dsl),
                    status="PUBLISHED", production_enabled=False, created_by="seed"))

        # 阻断清单
        for code, title, owner, impact in BLOCKERS:
            if not db.query(ExternalBlocker).filter_by(tenant_id=t.id,
                                                       blocker_code=code).first():
                db.add(ExternalBlocker(id=new_uuid(), tenant_id=t.id, blocker_code=code,
                                       title=title, owner_party=owner, impact=impact,
                                       status="OPEN"))
        # T14：令牌加固（禁用既有dev主体/引导管理员令牌轮换；幂等）
        created.update(_apply_token_hardening(db, t.id))
        db.commit()
    return created


def metrics_dual_workflow() -> dict:
    """metrics 双目标工作流（G23）：freeze→map→validate→{push回执, 视图发布}。"""
    return {
        "workflow_key": "metrics.dual_mock", "revision": 1,
        "trigger": "package_changed", "production_enabled": False,
        "nodes": [
            _node("freeze", "package.freeze", "metrics.package"),
            _node("map", "target.map", "profile.metrics_json"),
            _node("validate", "target.validate", "core.schema"),
            _node("send", "deliver.push", "destination.mock_http"),
            _node("receipt", "receipt.wait", "destination.mock_http"),
            _node("publish", "deliver.publish", "destination.view_publication"),
        ],
        "edges": [
            {"from": "freeze", "to": "map", "on": "success"},
            {"from": "map", "to": "validate", "on": "success"},
            {"from": "validate", "to": "send", "on": "success"},
            {"from": "send", "to": "receipt", "on": "success"},
            {"from": "validate", "to": "publish", "on": "success"},
        ],
    }


def seed_metrics_only(engine=None, session_factory=None, *,
                      with_volume: bool = True) -> dict:
    """metrics-only 种子（W11/G23）：不建病理字典/Pack/代理/连接器。

    本函数与 seed() 互不影响同一库的使用约定：metrics-only 进程使用
    独立数据库（BLYB_DATABASE_URL 指向全新sqlite/pg），表结构仅含核心迁移。
    """
    engine = engine or make_engine()
    sf = session_factory or make_session_factory(engine)
    created: dict[str, str] = {}
    disable_dev = os.environ.get("BLYB_DISABLE_DEV_TOKENS", "") == "1"
    with sf() as db:
        from core_db.models import Tenant
        t = db.query(Tenant).filter_by(code="first-hospital").first()
        if t is None:
            t = Tenant(id=new_uuid(), code="first-hospital", name="首院（合成演示）",
                       timezone="Asia/Shanghai")
            db.add(t)
            db.flush()
        created["tenant_id"] = t.id
        for code, kind, token, roles, name in DEV_SUBJECTS:
            if disable_dev:
                continue  # T14：禁用模式不创建 dev-* 演示主体
            if not db.query(AuthSubject).filter_by(tenant_id=t.id,
                                                   subject_code=code).first():
                db.add(AuthSubject(id=new_uuid(), tenant_id=t.id, kind=kind,
                                   subject_code=code, display_name=name,
                                   token_hash=hash_token(token), roles=roles))
        db.flush()
        src = db.query(SourceSystem).filter_by(tenant_id=t.id,
                                               code="metrics-csv").first()
        if src is None:
            src = SourceSystem(id=new_uuid(), tenant_id=t.id, code="metrics-csv",
                               kind="csv_import", identifier_namespace="import:metrics")
            db.add(src)
            db.flush()
        if with_volume:
            vol_root = os.path.join(HERE, "var", "volume-metrics")
            os.makedirs(vol_root, exist_ok=True)
            if not db.query(StorageVolume).filter_by(tenant_id=t.id,
                                                     name="vol1").first():
                vol_id = new_uuid()
                db.add(StorageVolume(id=vol_id, tenant_id=t.id, backend="filesystem",
                                     name="vol1", root_ref=vol_root,
                                     safety_margin_bytes=512 * 1024 * 1024,
                                     state="ACTIVE", health="OK"))
                db.flush()
                from storage_backend import VolumeHandle
                h = VolumeHandle(vol_id, vol_root, 0)
                h.ensure_layout()
                h.ensure_sentinel()
        if not db.query(Destination).filter_by(tenant_id=t.id, code="mock-push").first():
            db.add(Destination(id=new_uuid(), tenant_id=t.id, code="mock-push",
                               name="医保Mock推送（非真实平台）",
                               profile_revision="destination.mock_http@1.0",
                               mode="PUSH",
                               capabilities=["submit", "query", "receipt_poll"],
                               is_mock=True))
        if not db.query(Destination).filter_by(
                tenant_id=t.id, code="first-hospital-exchange").first():
            db.add(Destination(id=new_uuid(), tenant_id=t.id,
                               code="first-hospital-exchange",
                               name="交换发布视图（PULL）",
                               profile_revision="destination.view_publication@1.0",
                               mode="PULL_PUBLICATION",
                               capabilities=["view", "file_download"], is_mock=True))
        dsl = metrics_dual_workflow()
        if not db.query(WorkflowRevision).filter_by(
                tenant_id=t.id, workflow_key=dsl["workflow_key"],
                revision=dsl["revision"]).first():
            db.add(WorkflowRevision(
                id=new_uuid(), tenant_id=t.id, workflow_key=dsl["workflow_key"],
                revision=dsl["revision"], dsl=dsl, digest=digest_of(dsl),
                status="PUBLISHED", production_enabled=False, created_by="seed"))
        for code, title, owner, impact in BLOCKERS:
            if not db.query(ExternalBlocker).filter_by(tenant_id=t.id,
                                                       blocker_code=code).first():
                db.add(ExternalBlocker(id=new_uuid(), tenant_id=t.id,
                                       blocker_code=code, title=title,
                                       owner_party=owner, impact=impact,
                                       status="OPEN"))
        # T14：令牌加固（与 seed() 同语义；幂等）
        created.update(_apply_token_hardening(db, t.id))
        db.commit()
    return created


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    result = seed()
    print("seeded:", result)
