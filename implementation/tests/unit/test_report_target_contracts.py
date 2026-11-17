"""LT-01 契约常量测试：状态桶与映射完整性。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import (REPORT_BUCKETS, REPORT_BUCKET_LABELS,
                       SUBMISSION_STATE_TO_BUCKET, SubmissionState,
                       TargetLifecycle, TargetRuntimeKind)


def test_buckets_shape():
    assert len(REPORT_BUCKETS) == 8
    assert len(set(REPORT_BUCKETS)) == 8
    assert set(REPORT_BUCKET_LABELS) == set(REPORT_BUCKETS)
    assert all(isinstance(v, str) and v for v in REPORT_BUCKET_LABELS.values())


def test_lifecycle_enum():
    assert TargetLifecycle.ALL == ("PLANNED", "DEVELOPING", "MOCK_ONLY",
                                   "PILOT", "PRODUCTION", "SUSPENDED",
                                   "RETIRED")


def test_runtime_kind_enum():
    assert TargetRuntimeKind.ALL == ("PLATFORM", "EXTERNAL", "MANUAL")


def test_submission_state_mapping_complete():
    """SubmissionState 每个值都必须有桶映射（任务书步骤 2）。"""
    states = {v for k, v in vars(SubmissionState).items()
              if not k.startswith("_") and isinstance(v, str)}
    assert states == set(SUBMISSION_STATE_TO_BUCKET)
    for state in states:
        assert SUBMISSION_STATE_TO_BUCKET[state] in REPORT_BUCKETS


def test_bucket_targets_valid():
    assert set(SUBMISSION_STATE_TO_BUCKET.values()) <= set(REPORT_BUCKETS)
    assert SUBMISSION_STATE_TO_BUCKET["PREPARED"] == "PENDING"
    assert SUBMISSION_STATE_TO_BUCKET["CONFIRMED"] == "CONFIRMED"
