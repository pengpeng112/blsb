"""首院 HIS 连接器插件 his.medrec.oracle（10 §2.3）。"""
from __future__ import annotations

from connector_sdk import EncounterCandidate, PatientCandidate, SourceScope


class MedrecHisConnector:
    plugin_id = "his.medrec.oracle"

    def __init__(self, warehouse, *, schema: str = "MEDREC", page_limit: int = 20):
        self.wh = warehouse
        self.schema = schema
        self.page_limit = page_limit

    def get_patient(self, scope: SourceScope, patient_ref: str) -> list[PatientCandidate]:
        rows = self.wh.fetch_all("his.patient_by_id", {"patient_id": patient_ref},
                                 self.page_limit, schema=self.schema)
        return [PatientCandidate(
            source_object=f"{scope.object_ref('PAT_MASTER_INDEX')}",
            source_key=r["PATIENT_ID"], patient_id=r["PATIENT_ID"],
            id_type_raw=r.get("ID_TYPE"),
            id_no_masked=_mask_id(r.get("ID_NO")),
            id_no_full_ref=f"his.id:{r['PATIENT_ID']}:ID_NO" if r.get("ID_NO") else None,
            name_masked=_mask_name(r.get("NAME")),
            birth_date=r.get("DATE_OF_BIRTH"), gender_raw=r.get("SEX"))
            for r in rows]

    def get_encounter(self, scope: SourceScope, patient_ref: str,
                      encounter_ref: str | None, encounter_kind: str) -> list[EncounterCandidate]:
        """就诊查询必须精确：无 encounter_ref（VISIT_ID）时返回全部候选，不选最新。"""
        rows = self.wh.fetch_all("his.encounters_by_patient",
                                 {"patient_id": patient_ref}, self.page_limit,
                                 schema=self.schema)
        out = []
        for r in rows:
            if encounter_ref is not None and r["VISIT_ID"] != encounter_ref:
                continue
            out.append(EncounterCandidate(
                source_object=f"{scope.object_ref('PAT_VISIT')}",
                source_key=f"{r['PATIENT_ID']}+{r['VISIT_ID']}",
                patient_id=r["PATIENT_ID"], visit_id=r["VISIT_ID"],
                kind=encounter_kind, raw={k: str(v) for k, v in r.items()}))
        return out

    def get_source_dictionary(self, namespace: str, revision: str):
        """受控字典文件提供；本插件不内置'01=111'等映射（B05 阻断）。"""
        return None


def _mask_id(v):
    if not v:
        return None
    v = str(v)
    if len(v) <= 8:
        return "****"
    return v[:4] + "*" * (len(v) - 8) + v[-4:]


def _mask_name(name):
    if not name:
        return None
    name = str(name)
    return name[0] + "*" * (len(name) - 1) if len(name) > 1 else name + "*"
