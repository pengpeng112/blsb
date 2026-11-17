"""LT-01 manifest 自动发现测试（含坏 manifest / 重复 key 防护）。"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from plugins.app_runtime import PLUGIN_MANIFESTS, discover_manifests


def test_three_plugin_keys_preserved():
    # LT-05/B1 infectious；LT-07 medins_emr；LT-09 medins_imaging（合理期望更新）
    assert set(PLUGIN_MANIFESTS) == {"pathology", "metrics", "drug_trace",
                                     "infectious", "medins_emr",
                                     "medins_imaging"}


def test_manifest_fields_carried():
    dt = PLUGIN_MANIFESTS["drug_trace"]
    assert dt["module"] == "domains.drug_trace.plugin"
    assert dt["domain"] == "drug_trace"
    assert "collect" in dt["capabilities"]
    assert dt["report_targets"] == ["drug_trace_alihealth"]
    assert dt["ui"]["route"] == "/drugtrace"
    assert PLUGIN_MANIFESTS["pathology"]["report_targets"] == ["pathology_cloud"]
    assert PLUGIN_MANIFESTS["metrics"]["report_targets"] == []
    assert PLUGIN_MANIFESTS["metrics"]["ui"] is None


def test_discovery_missing_field_raises(tmp_path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "manifest.json").write_text(
        json.dumps({"key": "bad", "module": "x"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing"):
        discover_manifests(tmp_path)


def test_discovery_duplicate_key_raises(tmp_path):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "manifest.json").write_text(
            json.dumps({"key": "same", "module": "m", "domain": "d",
                        "capabilities": [], "description": ""}),
            encoding="utf-8")
    with pytest.raises(RuntimeError, match="duplicate plugin key"):
        discover_manifests(tmp_path)


def test_discovery_roundtrip_ok(tmp_path):
    d = tmp_path / "ok"
    d.mkdir()
    (d / "manifest.json").write_text(
        json.dumps({"key": "ok", "module": "m", "domain": "d",
                    "capabilities": ["c"], "description": "x",
                    "report_targets": [], "ui": None}), encoding="utf-8")
    out = discover_manifests(tmp_path)
    assert out["ok"]["key"] == "ok"
