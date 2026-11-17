"""Oracle empty-string cursor regression, using an isolated SQLite SQL engine.

The adapter converts string binds to Oracle's NULL semantics and translates only
the outer ROWNUM limit. The actual rendered WHERE/ORDER BY executes in SQL; no
Python implementation of its filtering is substituted. Live Oracle verification
is recorded separately; these tests do not claim SQLite is an Oracle server.
"""
from __future__ import annotations

import re
import sqlite3

import pytest

from domains.drug_trace.ingest import read_bill_whole
from sources.db_view.drug_trace import OracleDrugTraceWarehouse


class OracleStringCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, sql, params):
        sql = re.sub(r"\bWHERE ROWNUM <= (\d+)\s*$", r"LIMIT \1", sql)
        self.cursor.execute(sql, {k: None if v == "" else v
                                  for k, v in params.items()})

    @property
    def description(self):
        return self.cursor.description

    def fetchall(self):
        return self.cursor.fetchall()

    def close(self):
        self.cursor.close()


class OracleStringConnection:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)

    def cursor(self):
        return OracleStringCursor(self.conn.cursor())

    def close(self):
        self.conn.close()


KEY = {"source_system": "HIS", "pharmacy_code": "YF1",
       "business_kind": "UNKNOWN", "source_bill_id": "B0001"}
RECORD_IDS = ["0001", "0010", "01", "1", "10", "2"]


@pytest.fixture
def repository(tmp_path):
    path = tmp_path / "oracle-cursor-isolated.sqlite3"
    header = {**KEY, "source_bill_no": "0001", "source_revision": "r1",
              "warehouse_code": None, "supplier_code": None,
              "bill_time": "2026-10-22 08:00:00",
              "updated_at": "2026-10-22 08:00:00", "source_event_seq": None,
              "finalized": None, "is_void": None, "expected_code_count": None,
              "patient_ref": None, "encounter_ref": None,
              "source_bill_kind_raw": "PRESC_SOURCE=0;FLAG=1"}

    def code(record_id, index, **changes):
        return {**KEY, "source_revision": "r1", "source_line_id": "L0001",
                "source_code_record_id": record_id,
                "trace_code": f"{index:020d}", "quantity": None, "unit": None,
                "code_level": None, "parent_code": None,
                "updated_at": "2026-10-22 08:00:00", "is_deleted": 0,
                "f_trace_code": None, "s_trace_code": None,
                "t_trace_code": f"{index:020d}",
                "bill_time": "2026-10-22 08:00:00", **changes}

    expected = [code(rid, index) for index, rid in enumerate(RECORD_IDS, 1)]
    others = [code("0000", 90 + index, **{field: "OTHER"})
              for index, field in enumerate(KEY)]
    with sqlite3.connect(path) as conn:
        for name, rows in (("V_DRUG_TRACE_BILL", [header]),
                           ("V_DRUG_TRACE_CODE", expected + others)):
            fields = list(rows[0])
            conn.execute(f"CREATE TABLE {name} (" + ",".join(
                f"{field} INTEGER" if field == "is_deleted" else f"{field} TEXT"
                for field in fields) + ")")
            conn.executemany(f"INSERT INTO {name} VALUES (" +
                             ",".join("?" for _ in fields) + ")",
                             [[row[field] for field in fields] for row in rows])
    wh = OracleDrugTraceWarehouse(
        connection_factory=lambda: OracleStringConnection(path))
    return wh, header, expected


@pytest.mark.parametrize("initial", [None, ""])
def test_first_page_reads_codes_with_oracle_null_cursor(repository, initial):
    wh, _, expected = repository
    rows = wh.fetch_all("drugtrace.bill_codes",
                        {**KEY, "after_record_id": initial}, 2)
    assert [r["source_code_record_id"] for r in rows] == RECORD_IDS[:2]
    assert [r["trace_code"] for r in rows] == [r["trace_code"] for r in expected[:2]]


@pytest.mark.parametrize("page_size", [1, 2, 3, 10])
def test_whole_bill_reads_all_pages_without_duplicates(repository, page_size):
    wh, header, expected = repository
    rows, checked, reread = read_bill_whole(wh, header, code_page_limit=page_size)
    assert [r["source_code_record_id"] for r in rows] == RECORD_IDS
    assert [r["trace_code"] for r in rows] == [r["trace_code"] for r in expected]
    assert checked["source_revision"] == "r1"
    assert checked["business_kind"] == "UNKNOWN"
    assert checked["finalized"] is None
    assert reread is False


@pytest.mark.parametrize("field", list(KEY))
def test_first_page_preserves_each_composite_key_filter(repository, field):
    wh, _, _ = repository
    rows = wh.fetch_all("drugtrace.bill_codes",
                        {**KEY, field: "OTHER", "after_record_id": None}, 100)
    assert len(rows) == 1
    assert rows[0][field] == "OTHER"
    assert rows[0]["source_code_record_id"] == "0000"


def test_nonempty_cursor_keeps_string_order_and_excludes_boundary(repository):
    wh, _, _ = repository
    rows = wh.fetch_all("drugtrace.bill_codes",
                        {**KEY, "after_record_id": "01"}, 100)
    assert [r["source_code_record_id"] for r in rows] == ["1", "10", "2"]


def test_empty_bill_stays_empty(repository):
    wh, _, _ = repository
    assert wh.fetch_all("drugtrace.bill_codes",
                        {**KEY, "source_bill_id": "ABSENT",
                         "after_record_id": None}, 100) == []


def _poll(wh, **cursor):
    return wh.fetch_all("drugtrace.poll_bills", {
        "begin_time": "2026-10-22 08:00:00", "end_time": "2026-10-22 09:00:00",
        "since_time": "2026-10-22 08:00:00", "since_ss": "",
        "since_pc": "", "since_bk": "", "since_sb": "", **cursor}, 100)


def test_header_exact_window_begin_is_included_with_null_first_key(repository):
    wh, header, _ = repository
    assert _poll(wh) == [header]
    assert _poll(wh, since_ss=None) == [header]


def test_header_populated_cursor_excludes_seen_equal_key(repository):
    wh, _, _ = repository
    assert _poll(wh, since_ss="HIS", since_pc="YF1", since_bk="UNKNOWN",
                 since_sb="B0001") == []
    assert len(_poll(wh, since_ss="HIS", since_pc="YF1", since_bk="UNKNOWN",
                     since_sb="B0000")) == 1


def test_header_null_cursor_keeps_half_open_time_window(repository):
    wh, _, _ = repository
    assert _poll(wh, end_time="2026-10-22 08:00:00") == []
    assert _poll(wh, begin_time="2026-10-22 08:00:01") == []


def test_bound_reader_rejects_wrong_database_account(repository):
    wh, _, _ = repository
    wh._expected_user = "BLYB_DRUGTRACE"
    from connector_sdk import CapabilityError
    with pytest.raises(CapabilityError, match="reader does not match"):
        _poll(wh)
