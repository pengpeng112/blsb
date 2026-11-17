"""Source facts must never turn metadata access into a live collection approval."""
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from connector_sdk import CapabilityError
from sources.db_view.source_profiles import HIS_TRACE_20261019_R1 as PROFILE


def test_pending_profile_cannot_open_oracle_even_when_dsn_exists(monkeypatch):
    from domains.drug_trace.service import make_warehouse
    monkeypatch.setenv("BLYB_DRUGTRACE_ORACLE_DSN", "synthetic-not-a-real-dsn")
    with patch("sources.db_view.drug_trace.OracleDrugTraceWarehouse") as factory:
        with pytest.raises(CapabilityError, match="metadata only"):
            make_warehouse({"mode": "oracle", "source_profile": PROFILE.key})
        factory.assert_not_called()


@pytest.mark.parametrize("config", [
    {"mode": "oracle", "source_profile": "missing-revision"},
    {"mode": "oracle", "source_profile": ""},
    {"mode": "synthetic", "source_profile": PROFILE.key},
])
def test_unknown_or_incompatible_profile_is_rejected(config):
    from domains.drug_trace.service import make_warehouse
    with pytest.raises(CapabilityError):
        make_warehouse(config)


def test_revision_cannot_be_relabelled_as_live():
    with pytest.raises(FrozenInstanceError):
        PROFILE.evidence_level = "LIVE_CONTRACT_VERIFIED"


def test_existing_synthetic_configuration_still_works():
    from domains.drug_trace.service import make_warehouse
    warehouse = make_warehouse({"mode": "synthetic", "namespace": "LOCAL"})
    assert warehouse.dialect() == "synthetic"
    assert warehouse.namespace == "LOCAL"


def test_live_probe_profile_still_cannot_open_oracle(monkeypatch):
    from domains.drug_trace.service import make_warehouse
    from sources.db_view.source_profiles import HIS_TRACE_20261019_R2
    monkeypatch.setenv("BLYB_DRUGTRACE_ORACLE_DSN", "synthetic-not-a-real-dsn")
    with patch("sources.db_view.drug_trace.OracleDrugTraceWarehouse") as factory:
        with pytest.raises(CapabilityError, match="limited live evidence"):
            make_warehouse({"mode": "oracle", "source_profile": HIS_TRACE_20261019_R2.key,
                            "verified": True, "write_policy": "readonly", "unresolved": []})
        factory.assert_not_called()
