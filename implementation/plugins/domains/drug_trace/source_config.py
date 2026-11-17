"""Audited, versioned source configuration; credentials stay in worker env."""
from contracts import ApiError, new_uuid
from contracts.api import digest_of
from core_db.models import ConfigRevision, ConnectorInstance, Destination

from . import contracts as C
from . import service as S


def validate(content: dict) -> list[str]:
    from connector_sdk import CapabilityError
    from sources.db_view.source_profiles import get_source_profile
    if not isinstance(content, dict):
        return ["content must be an object"]
    allowed = {"mode", "source_profile", "dsn_env", "objects",
               "collection_mode", "namespace"}
    if set(content) - allowed:
        return ["unsupported source configuration fields (credentials forbidden)"]
    if content.get("mode") == "synthetic":
        if set(content) - {"mode", "namespace", "collection_mode"}:
            return ["synthetic source cannot bind Oracle credentials or objects"]
        if content.get("collection_mode", "batch") != "batch":
            return ["synthetic source supports batch only"]
        return []
    if content.get("mode") != "oracle":
        return ["mode must be synthetic or oracle"]
    try:
        profile = get_source_profile(str(content.get("source_profile", "")))
        profile.require_runtime_ready(config=content)
    except CapabilityError as exc:
        return [exc.code + ": " + exc.message]
    return []


def connector(db, tenant_id, connector_id):
    obj = db.get(ConnectorInstance, connector_id)
    if obj is None or obj.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "source connector not found")
    if obj.direction != "source" or obj.plugin_id != "sources.db_view.drug_trace":
        raise ApiError("CAPABILITY_UNSUPPORTED", "not a drug trace source")
    return obj


def publish(db, tenant_id, connector_id, content, *, publish=False,
            enable=False, actor=""):
    obj = connector(db, tenant_id, connector_id)
    problems = validate(content)
    if problems:
        raise ApiError("VALIDATION_FAILED", "; ".join(problems))
    if enable and not publish:
        raise ApiError("VALIDATION_FAILED", "enable requires publish")
    if enable and content["mode"] == "oracle":
        S.require_production_evidence(db, tenant_id, profile=C.PROFILE_RETAIL,
                                      capability="collect")
    revisions = (db.query(ConfigRevision).filter_by(
        tenant_id=tenant_id, config_type="connector", owner_id=obj.id)
        .order_by(ConfigRevision.revision.desc()).all())
    digest = digest_of(content)
    cr = next((r for r in revisions if r.digest == digest and
               (r.state == "DRAFT" or (r.state == "PUBLISHED" and
                                       obj.active_config_revision == r.id))), None)
    created = cr is None
    if cr is None:
        cr = ConfigRevision(id=new_uuid(), tenant_id=tenant_id,
                            config_type="connector", owner_id=obj.id,
                            revision=(revisions[0].revision + 1 if revisions else 1),
                            content_json=content, digest=digest, state="DRAFT",
                            created_by=actor)
        db.add(cr)
    if publish:
        for old in revisions:
            if old.id != cr.id and old.state == "PUBLISHED":
                old.state = "RETIRED"
        cr.state = "PUBLISHED"
        obj.active_config_revision = cr.id
        # Publishing alone does not silently enable collection.
        obj.status = "ENABLED" if enable else "DRAFT"
    db.flush()
    return {"config_revision_id": cr.id, "revision": cr.revision,
            "state": cr.state, "connector_status": obj.status, "created": created}


def activate_destination(db, tenant_id, dest_id, *, enabled):
    obj = db.get(Destination, dest_id)
    if obj is None or obj.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "destination not found")
    profile = (obj.profile_revision or "").split("@")[0]
    if profile not in C.PROFILES:
        raise ApiError("CAPABILITY_UNSUPPORTED", "not a drug trace destination")
    if enabled:
        if not obj.config_revision:
            raise ApiError("PRECONDITION_REQUIRED", "published config required")
        cfg = S._load_config_revision(db, tenant_id, obj.config_revision,
                                      owner_id=obj.id)
        problems = S.validate_destination_config(cfg.content_json, is_mock=obj.is_mock)
        if problems:
            raise ApiError("VALIDATION_FAILED", "; ".join(problems))
        if not obj.is_mock:
            S.require_production_evidence(
                db, tenant_id, profile=profile, capability="send",
                destination_id=obj.id, config_release_id=obj.config_revision)
    obj.status = "ENABLED" if enabled else "DISABLED"
    db.flush()
    return {"destination_id": obj.id, "status": obj.status}
