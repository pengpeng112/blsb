"""首院 PIS 连接器插件 pis.pitaya.sqlserver（10 §2.1—§2.2）。

核心不 import 本模块；病理领域插件通过 connector_sdk 协议调用。
"""
from __future__ import annotations

from connector_sdk import (ApplicationCandidate, CapabilityError, CaseRecord,
                           ReportRecord, SlideCandidate, SourceScope)


class PitayaPisConnector:
    plugin_id = "pis.pitaya.sqlserver"

    def __init__(self, warehouse, *, database: str = "PITAYA", schema: str = "dbo",
                 barcode_field: str = "qptmh", page_limit: int = 20):
        self.wh = warehouse
        self.database = database
        self.schema = schema
        self.barcode_field = barcode_field
        self.page_limit = page_limit

    # ---------------------------------------------------------------- 能力
    def get_slide_by_barcode(self, scope: SourceScope, barcode_raw: str,
                             case_context: str | None = None) -> dict:
        """0/1/N 均合法；返回候选数组并标记是否截断（TOP(2) 语义）。"""
        if not barcode_raw:
            raise CapabilityError("CAPABILITY_UNSUPPORTED", "barcode_raw required")
        rows = self.wh.fetch_all("pis.slide_by_barcode", {"barcode": barcode_raw}, 2,
                                 database=self.database, schema=self.schema)
        truncated = len(rows) > 1  # TOP(2)：两条即证明非唯一，不代表完整候选清单
        candidates = []
        for r in rows:
            candidates.append(SlideCandidate(
                source_object=f"{scope.object_ref('Pitaya_Slice')}",
                source_key=str(r["Id"]),
                barcode_raw=str(r[self.barcode_field]),
                pathology_no=str(r["PathologyID"]),
                block_key=str(r["lkh"]) if r.get("lkh") is not None else None,
                status_raw={k: r.get(k) for k in ("qpzt", "zpzt", "tmdyzt") if k in r},
            ))
        return {"candidates": candidates, "truncated": truncated,
                "barcode_raw": barcode_raw}

    def get_case(self, scope: SourceScope, case_source_key: str) -> CaseRecord | None:
        rows = self.wh.fetch_all("pis.case_by_pathology",
                                 {"pathology_no": case_source_key}, self.page_limit,
                                 database=self.database, schema=self.schema)
        if not rows:
            return None
        if len(rows) > 1:
            raise CapabilityError("SOURCE_AMBIGUOUS",
                                  f"multiple cases for {case_source_key}")
        r = rows[0]
        return CaseRecord(
            source_object=scope.object_ref("Pitaya"),
            source_key=str(r["PathologyID"]),
            pathology_no=str(r["PathologyID"]),
            patient_source_id=_s(r.get("PatientID")),
            in_patient_id=_s(r.get("InPatientID")),
            out_patient_id=_s(r.get("OutPatientID")),
            req_note_id=_s(r.get("ReqNoteID")),
            identity_card=_s(r.get("IdentityCard")),
            name_masked=_mask_name(r.get("PatientName")),
            create_time=_s(r.get("CreateTime")),
            application_time=None,  # 权威申请时间字段未确认（B03阻断），不用CreateTime顶替
            spec_recv_time=_s(r.get("SpecRecvTime")),
            report_time=_s(r.get("ReportTime")),
            verify_report_time=_s(r.get("VerifyReportTime")),
            status_raw=_s(r.get("Status")),
            category_raw=_s(r.get("FreezingID")),
            raw={k: _s(v) for k, v in r.items()
                 if k not in ("IdentityCard", "PatientName")})

    def list_slides(self, scope: SourceScope, case_source_key: str, cursor: int = 0):
        """keyset 分页读取玻片（R31）：以 Id 为游标逐页取，调用方循环到短页为止。
        cursor 语义：返回 Id > cursor 的下一页（升序）。"""
        rows = self.wh.fetch_all("pis.slides_by_case",
                                 {"pathology_no": case_source_key,
                                  "after_id": cursor}, self.page_limit,
                                 database=self.database, schema=self.schema)
        out = [SlideCandidate(
            source_object=scope.object_ref("Pitaya_Slice"),
            source_key=str(r["Id"]), barcode_raw=str(r[self.barcode_field]),
            pathology_no=str(r["PathologyID"]),
            block_key=str(r["lkh"]) if r.get("lkh") is not None else None,
            status_raw={k: r.get(k) for k in ("qpzt", "zpzt", "tmdyzt") if k in r})
            for r in rows]
        return out, (int(out[-1].source_key) if out else None)

    def iter_slides(self, scope: SourceScope, case_source_key: str):
        """完整遍历：翻页直到短页；返回 (全部候选, 页数, 末页是否证明为空)。"""
        all_out: list = []
        cursor = 0
        pages = 0
        while True:
            batch, cursor = self.list_slides(scope, case_source_key, cursor)
            pages += 1
            all_out.extend(batch)
            if len(batch) < self.page_limit:
                return all_out, pages, True
            if not cursor:
                return all_out, pages, False

    def get_applications(self, scope: SourceScope, req_note_id: str | None):
        if not req_note_id:
            return []
        rows = self.wh.fetch_all("pis.applications_by_reqnote",
                                 {"req_note_id": req_note_id}, self.page_limit,
                                 database=self.database, schema=self.schema)
        return [ApplicationCandidate(
            source_object=scope.object_ref("患者检查申请信息表"),
            source_key=r["申请号"], external_id=r["申请号"],
            requested_at=_s(r.get("申请日期")),
            encounter_kind_raw=_s(r.get("病人类型")),
            category_raw=_s(r.get("检查类型")),
            request_org_raw=_s(r.get("医疗机构编码")),
            status_raw=_s(r.get("状态")),
            void_info={"作废人": _s(r.get("作废人")), "作废原因": _s(r.get("作废原因")),
                       "作废时间": _s(r.get("作废时间")), "删除标记": r.get("删除标记")},
            raw={k: _s(v) for k, v in r.items()}) for r in rows]

    def get_reports(self, scope: SourceScope, pathology_no: str):
        rows = self.wh.fetch_all(
            "pis.reports_by_case",
            {"pathology_no": pathology_no, "pathology_like": pathology_no + "-%"},
            self.page_limit, database=self.database, schema=self.schema)
        return [ReportRecord(
            source_object=scope.object_ref("患者检查报告信息表"),
            source_key=r["报告流水号"], pathology_no=pathology_no,
            report_serial=r["报告流水号"],
            gross=_s(r.get("Rysj")), microscopic=_s(r.get("Gjsj")),
            diagnosis=_s(r.get("病理诊断")),
            issued_at=_s(r.get("审核日期")), audit_time=_s(r.get("审核日期")),
            report_org_raw=_s(r.get("医疗机构编码")),
            image_url=_s(r.get("图像报告URL")),
            voided=bool(r.get("作废标记")),
            raw={k: _s(v) for k, v in r.items()}) for r in rows]

    def get_report_texts(self, scope: SourceScope, pathology_no: str):
        """主表三分段文本（Rysj/Gjsj/Zdyj）作为报告正文的另一来源。"""
        case = self.get_case(scope, pathology_no)
        if case is None:
            return None
        return {"gross": case.raw.get("Rysj"), "microscopic": case.raw.get("Gjsj"),
                "diagnosis": case.raw.get("Zdyj")}

    def poll_changes(self, scope: SourceScope, stream: str, checkpoint: dict, limit: int):
        """CreateTime 通道：仅新增发现（INSERT_DISCOVERY_ONLY）。

        R11：游标为 (CreateTime, PathologyID) 复合键——since 为开区间下界（不含），
        同刻多行按 PathologyID 续读不丢；本方法原地更新 checkpoint
        （since 推进 + since_key 记录本页末键），调用方与本页 Inbox/Outbox 原子提交。
        """
        since = checkpoint.get("since", "0000-01-01T00:00:00")
        until = checkpoint.get("until", "9999-12-31T23:59:59")

        def _fetch(after: str):
            return self.wh.fetch_all("pis.poll_cases_created",
                                     {"since": since, "until": until,
                                      "after_key": after}, limit,
                                     database=self.database, schema=self.schema)

        rows = _fetch(checkpoint.get("since_key", ""))
        if not rows and checkpoint.get("since_key"):
            # 同刻窗口已读完且调用方游标仍停在同一时刻：回卷该窗口重读
            # （至少一次语义；重复发现由下游事件聚合键/作业执行键去重吸收）
            checkpoint["since_key"] = ""
            rows = _fetch("")
        out = [{"source_key": r["PathologyID"], "create_time": r["CreateTime"]}
               for r in rows]
        if out:
            last = out[-1]
            if last["create_time"] > since:
                checkpoint["since"] = last["create_time"]
                checkpoint["since_key"] = last["source_key"]
            else:
                checkpoint["since_key"] = last["source_key"]
        return out

    def reconcile_partition(self, scope: SourceScope, key_from: str, key_to: str,
                            limit: int, after_key: str = ""):
        """分区盘点按 keyset 取页（R10）：返回 (keys, 是否证明为末页)。"""
        rows = self.wh.fetch_all("pis.scan_partition_cases",
                                 {"key_from": key_from, "key_to": key_to,
                                  "after_key": after_key}, limit,
                                 database=self.database, schema=self.schema)
        keys = [str(r["PathologyID"]) for r in rows]
        return keys, len(rows) < limit

    def get_images(self, scope: SourceScope, pathology_no: str):
        rows = self.wh.fetch_all("pis.images_by_exam",
                                 {"pathology_no": pathology_no}, self.page_limit,
                                 database=self.database, schema=self.schema)
        return [{"url": r.get("CCLJ"), "device_type": r.get("SBLX"),
                 "storage": r.get("CCFS")} for r in rows]


def _s(v):
    return None if v is None else str(v)


def _mask_name(name):
    if not name:
        return None
    name = str(name)
    if len(name) <= 1:
        return name + "*"
    return name[0] + "*" * (len(name) - 1)
