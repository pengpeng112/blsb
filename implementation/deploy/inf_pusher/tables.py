"""表注册：视图 → 前置机 API 的字段映射、必填集、日期字段。

列名转 JSON 键规则：SNAKE_CASE → camelCase（特殊名在 map 里显式给出）。
日期列统一序列化为 'YYYY-MM-DD HH:mm:ss'（规范格式，2026-10-28 端点实测通过）。
"""

# 通用列（六张表共有语义，逐表仍显式声明以保持可审计）
def _common(activity=False):
    m = {
        "ID": "id", "PATIENT_ID": "patientId", "SERIAL_NUMBER": "serialNumber",
        "PATIENT_NAME": "patientName",
        "ID_CARD_TYPE_CODE": "idCardTypeCode", "ID_CARD_TYPE_NAME": "idCardTypeName",
        "ID_CARD": "idCard",
        "ORG_CODE": "orgCode", "ORG_NAME": "orgName",
        "DEPT_CODE": "deptCode", "DEPT_NAME": "deptName",
        "OPERATOR_ID": "operatorId", "OPERATION_TIME": "operationTime",
    }
    if activity:
        m.update({"ACTIVITY_TYPE_CODE": "activityTypeCode",
                  "ACTIVITY_TYPE_NAME": "activityTypeName"})
    return m


DATE_FIELDS = ("operationTime", "admissionDate", "dischargeDate", "deadDate",
               "createTime", "symptomStartDate", "symptomEndDate",
               "examinationReportDate", "activityTime")

# 规范必填（发送前客户端预检；与 FIELD_MAPPING §1-§5 一致）
TABLES = {
    "death": {
        "view": "ODS.V_INF_DEATH_INFO",
        "endpoint": "emrDeathInfo",
        "required": ["id", "patientId", "activityTypeCode", "activityTypeName",
                     "serialNumber", "patientName", "idCardTypeCode",
                     "idCardTypeName", "idCard", "deadDate", "directCauseCode",
                     "directCauseName", "orgCode", "orgName", "deptCode",
                     "deptName", "operatorId", "operationTime"],
        "map": dict(_common(activity=True), **{
            "TREATMENT_DESC": "treatmentDesc", "DEAD_DATE": "deadDate",
            "DIRECT_CAUSE_CODE": "directCauseCode",
            "DIRECT_CAUSE_NAME": "directCauseName",
            "DEATH_DIAGNOSIS_CODE": "deathDiagnosisCode",
            "DEATH_DIAGNOSIS_NAME": "deathDiagnosisName",
            "CHIEF_PHYSICIAN_ID": "chiefPhysicianId",
        }),
        "watermark": "OPERATION_TIME", "overlap_days": 3, "full_days": 180,
    },
    "admission": {
        "view": "ODS.V_INF_ADMISSION_RECORD",
        "endpoint": "emrAdmissionRecord",
        "required": ["id", "patientId", "serialNumber", "patientName",
                     "idCardTypeCode", "idCardTypeName", "idCard",
                     "admissionDate", "orgCode", "orgName", "deptCode",
                     "deptName", "operatorId", "operationTime",
                     "wmOutpatientDiagnosisCode", "wmOutpatientDiagnosisName"],
        "map": dict(_common(), **{
            "HEALTH_CARD_NO": "healthCardNo",
            "PAY_METHOD_CODE": "payMethodCode", "PAY_METHOD_NAME": "payMethodName",
            "ADMISSION_NUM": "admissionNum", "REG_NO": "regNo",
            "ADMISSION_DATE": "admissionDate",
            "ADMISSION_DEPT_CODE": "admissionDeptCode",
            "ADMISSION_DEPT_NAME": "admissionDeptName",
            "WARD_NO": "wardNo", "DISCHARGE_DATE": "dischargeDate",
            "DISCHARGE_DEPT_CODE": "dischargeDeptCode",
            "DISCHARGE_DEPT_NAME": "dischargeDeptName",
            "DISCHARGE_WARD": "dischargeWard", "ADMISSION_DAYS": "admissionDays",
            "WM_OUTPATIENT_DIAGNOSIS_CODE": "wmOutpatientDiagnosisCode",
            "WM_OUTPATIENT_DIAGNOSIS_NAME": "wmOutpatientDiagnosisName",
            "DISCHARGE_DIAGNOSIS_CODE": "dischargeDiagnosisCode",
            "DISCHARGE_DIAGNOSIS_NAME": "dischargeDiagnosisName",
            "WM_OTHER_DIAGNOSIS_CODE": "wmOtherDiagnosisCode",
            "WM_OTHER_DIAGNOSIS_NAME": "wmOtherDiagnosisName",
            "EXTERNAL_CAUSES_CODE": "externalCausesCode",
            "EXTERNAL_CAUSES_NAME": "externalCausesName",
            "EXTERNAL_CAUSES_SYSTEM_NAME": "externalCausesSystemName",
            "PATHOLOGICAL_DIAGNOSIS_CODE": "pathologicalDiagnosisCode",
            "PATHOLOGICAL_DIAGNOSIS_NAME": "pathologicalDiagnosisName",
            "PATHOLOGICAL_NO": "pathologicalNo",
            "ALLERGY_CODE": "allergyCode", "ALLERGY_DRUG": "allergyDrug",
            "AUTOPSY_CODE": "autopsyCode",
            "CHIEF_PHYSICIAN_ID": "chiefPhysicianId",
            "RESIDENT_PHYSICIAN_ID": "residentPhysicianId",
            "ACCEPT_ORG_CODE": "acceptOrgCode", "ACCEPT_ORG_NAME": "acceptOrgName",
        }),
        "watermark": "OPERATION_TIME", "overlap_days": 3, "full_days": 180,
        "quality_watch": {"null_warn": ["wmOutpatientDiagnosisCode",
                                        "dischargeDiagnosisCode"]},
    },
    "first_course": {
        "view": "ODS.V_INF_FIRST_COURSE",
        "endpoint": "emrFirstCourse",
        "required": ["id", "patientId", "serialNumber", "patientName",
                     "idCardTypeCode", "idCardTypeName", "idCard",
                     "orgCode", "orgName", "deptCode", "deptName",
                     "operatorId", "operationTime"],
        "map": dict(_common(), **{
            "WARD_NAME": "wardName", "WARD_NO": "wardNo", "BED_NO": "bedNo",
            "CREATE_TIME": "createTime", "CHIEF_COMPLAINT": "chiefComplaint",
            "PRESENT_ILLNESS_HIS": "presentIllnessHis",
            "OBSERVATION_RESULT": "observationResult",
            "DIAGNOSIS_BASIS": "diagnosisBasis",
            "WM_INITAL_DIAGNOSIS_CODE": "wmInitalDiagnosisCode",
            "WM_INITAL_DIAGNOSIS_NAME": "wmInitalDiagnosisName",
            "TCM_INITAL_DIAGNOSIS_CODE": "tcmInitalDiagnosisCode",
            "TCM_INITAL_DIAGNOSIS_NAME": "tcmInitalDiagnosisName",
            "TCM_INITAL_SYNDROME_CODE": "tcmInitalSyndromeCode",
            "TCM_INITAL_SYNDROME_NAME": "tcmInitalSyndromeName",
            "WM_DIFF_DIAGNOSIS_CODE": "wmDiffDiagnosisCode",
            "WM_DIFF_DIAGNOSIS_NAME": "wmDiffDiagnosisName",
            "TCM_PRIMARY_DIAGNOSIS_CODE": "tcmPrimaryDiagnosisCode",
            "TCM_PRIMARY_DIAGNOSIS_NAME": "tcmPrimaryDiagnosisName",
            "TCM_PRIMARY_SYNDROME_CODE": "tcmPrimarySyndromeCode",
            "TCM_PRIMARY_SYNDROME_NAME": "tcmPrimarySyndromeName",
            "TREATMENT": "treatment", "TREATMENT_PLAN": "treatmentPlan",
            "DISEASE_PROGRESSION_CODE": "diseaseProgressionCode",
            "DISEASE_PROGRESSION_NAME": "diseaseProgressionName",
            "RESIDENT_PHYSICIAN_ID": "residentPhysicianId",
        }),
        "watermark": "OPERATION_TIME", "overlap_days": 3, "full_days": 30,
        "truncation_fields": {"presentIllnessHis": 2000},
    },
    "daily_course": {
        "view": "ODS.V_INF_DAILY_COURSE",
        "endpoint": "emrDailyCourse",
        "required": ["id", "patientId", "serialNumber", "patientName",
                     "idCardTypeCode", "idCardTypeName", "idCard",
                     "orgCode", "orgName", "deptCode", "deptName",
                     "operatorId", "operationTime"],
        "map": dict(_common(), **{
            "WARD_NAME": "wardName", "WARD_NO": "wardNo", "BED_NO": "bedNo",
            "CREATE_TIME": "createTime", "COURSE": "course",
            "ORDER_CONTENT": "orderContent",
            "OBSERVATION_RESULT": "observationResult",
            "TREATMENT": "treatment",
            "DISEASE_PROGRESSION_CODE": "diseaseProgressionCode",
            "DISEASE_PROGRESSION_NAME": "diseaseProgressionName",
        }),
        "watermark": "OPERATION_TIME", "overlap_days": 3, "full_days": 30,
        "truncation_fields": {"course": 2000},
    },
    "exam": {
        "view": "ODS.V_INF_EX_CLINICAL",
        "endpoint": "emrExClinical",
        "required": ["id", "patientId", "activityTypeCode", "activityTypeName",
                     "serialNumber", "patientName", "idCardTypeCode",
                     "idCardTypeName", "idCard", "applyDeptCode",
                     "applyDeptName", "examinationTypeCode",
                     "examinationTypeName", "orgCode", "orgName", "deptCode",
                     "deptName", "operatorId", "operationTime"],
        "map": dict(_common(activity=True), **{
            "WARD_NAME": "wardName", "WARD_NO": "wardNo", "BED_NO": "bedNo",
            "APPLICATION_FORM_NO": "applicationFormNo",
            "APPLY_ORG_CODE": "applyOrgCode", "APPLY_ORG_NAME": "applyOrgName",
            "APPLY_DEPT_CODE": "applyDeptCode", "APPLY_DEPT_NAME": "applyDeptName",
            "SYMPTOM_START_DATE": "symptomStartDate",
            "SYMPTOM_END_DATE": "symptomEndDate",
            "SYMPTOM_DESC": "symptomDesc", "TREATMENT_DESC": "treatmentDesc",
            "SPECIAL_EXAMINATION_CODE": "specialExaminationCode",
            "EXAMINATION_TYPE_CODE": "examinationTypeCode",
            "EXAMINATION_TYPE_NAME": "examinationTypeName",
            "EXAMINATION_OBJECTIVE_DESC": "examinationObjectiveDesc",
            "EXAMINATION_SUBJECTIVE_DESC": "examinationSubjectiveDesc",
            "EXAMINATION_NOTES": "examinationNotes",
            "EXAMINATION_REPORT_NO": "examinationReportNo",
            "EXAMINATION_REPORT_DATE": "examinationReportDate",
            "EXAMINATION_REPORT_ID": "examinationReportId",
        }),
        "watermark": "OPERATION_TIME", "overlap_days": 2, "full_days": 60,
    },
    "exam_item": {
        "view": "ODS.V_INF_EX_CLINICAL_ITEM",
        "endpoint": "emrExClinicalItem",
        "required": ["id", "exClinicalId", "itemCode", "itemName",
                     "operatorId", "operationTime"],
        "map": {
            "ID": "id", "EX_CLINICAL_ID": "exClinicalId",
            "ITEM_CODE": "itemCode", "ITEM_NAME": "itemName",
            "EXAMINATION_RESULT_CODE": "examinationResultCode",
            "EXAMINATION_RESULT_NAME": "examinationResultName",
            "EXAMINATION_QUANTIFICATION": "examinationQuantification",
            "EXAM_QUANT_UNIT": "examinationQuantificationUnit",
            "OPERATOR_ID": "operatorId", "OPERATION_TIME": "operationTime",
        },
        "watermark": "OPERATION_TIME", "overlap_days": 5, "full_days": 60,
        "depends_on": "exam",  # 先主表后子表（发送顺序约定）
        "parent_fk": "exClinicalId",   # PT-01/R2：补齐子表父门禁键（exam_item.exClinicalId→exam.id），
        # 与 ex_lab_item(exLabId)/order_item(orderId) 门禁口径一致（此前仅声明依赖未参与门禁）
    },
    # ---- 2026-11-14 新增 5 接口（检验/医嘱/生命体征，INF-5VIEW-LIVE-20261114）----
    # 注册段源：docs/development/inf-report-20261027/sql/tables-register-5views.py
    # 列名与 V_INF_* 视图一致（含 CRBSB 沿用的拼写截断列名 EEXAMINATION_SUBJECTIVE_DESC /
    # EXAMINATION_QUANTIFICA_* / EXCUTE_DATE / VENTILATORUSED_*，map 按实际列名映射）。
    # 字符串日期列在视图层已统一 TO_CHAR 'YYYY-MM-DD HH24:MI:SS'，OPERATION_TIME 为 DATE 列。
    "ex_lab": {
        "view": "ODS.V_INF_EX_LAB", "endpoint": "emrExLab",
        "required": ["id", "patientId", "activityTypeCode", "activityTypeName", "serialNumber",
                     "patientName", "idCardTypeCode", "idCardTypeName", "idCard",
                     "applyDeptCode", "applyDeptName", "examinationReportNo",
                     "orgCode", "orgName", "deptCode", "deptName", "operatorId", "operationTime"],
        "map": dict(_common(activity=True), **{
            "WARD_NAME": "wardName", "WARD_NO": "wardNo", "BED_NO": "bedNo",
            "APPLICATION_FORM_NO": "applicationFormNo",
            "APPLY_DEPT_CODE": "applyDeptCode", "APPLY_DEPT_NAME": "applyDeptName",
            "APPLY_ORG_CODE": "applyOrgCode", "APPLY_ORG_NAME": "applyOrgName",
            "APPLY_PHYSICIAN_ID": "applyPhysicianId",
            "SPECIMEN_CATEGORY_CODE": "specimenCategoryCode", "SPECIMEN_CATEGORY_NAME": "specimenCategoryName",
            "SPECIMEN_NO": "specimenNo",
            "SPECIMEN_SAMPLING_DATE": "specimenSamplingDate", "SPECIMEN_RECEIVING_DATE": "specimenReceivingDate",
            "EXAMINATION_PHYSICIAN_ID": "examinationPhysicianId", "EXAMINATION_DATE": "examinationDate",
            "EXAMINATION_REPORT_NO": "examinationReportNo",
            "EXAMINATION_OBJECTIVE_DESC": "examinationObjectiveDesc",
            "EEXAMINATION_SUBJECTIVE_DESC": "examinationSubjectiveDesc",
            "EXAMINATION_NOTES": "examinationNotes",
            "EXAMINATION_REPORT_DATE": "examinationReportDate", "EXAMINATION_REPORT_ID": "examinationReportId",
        }),
        "watermark": "OPERATION_TIME", "overlap_days": 2, "full_days": 60,
    },
    "ex_lab_item": {
        "view": "ODS.V_INF_EX_LAB_ITEM", "endpoint": "emrExLabItem",
        "required": ["id", "exLabId", "itemCode", "itemName", "operatorId", "operationTime"],
        "map": {
            "ID": "id", "EX_LAB_ID": "exLabId", "ITEM_CODE": "itemCode", "ITEM_NAME": "itemName",
            "SOURCE_EXAMINATION_RESULT_CODE": "sourceExaminationResultCode",
            "SOURCE_EXAMINATION_RESULT_NAME": "sourceExaminationResultName",
            "EXAMINATION_RESULT_CODE": "examinationResultCode", "EXAMINATION_RESULT_NAME": "examinationResultName",
            "EXAMINATION_QUANTIFICATION": "examinationQuantification",
            "EXAMINATION_QUANTIFICA_UNIT": "examinationQuantificationUnit",
            "EXAMINATION_QUANTIFICA_LOWER": "examinationQuantificationLower",
            "EXAMINATION_QUANTIFICA_UPPER": "examinationQuantificationUpper",
            "EXAMINATION_QUANTIFICATION_RI": "examinationQuantificationRi",
            "OPERATOR_ID": "operatorId", "OPERATION_TIME": "operationTime",
        },
        "watermark": "OPERATION_TIME", "overlap_days": 2, "full_days": 60, "depends_on": "ex_lab",
        "parent_fk": "exLabId",   # P2 父受理门禁：子项发送前核对父（ex_lab.id=TEST_NO）本进程受理证据
    },
    "order": {
        "view": "ODS.V_INF_ORDER", "endpoint": "emrOrder",
        "required": ["id", "patientId", "activityTypeCode", "activityTypeName", "serialNumber",
                     "patientName", "idCardTypeCode", "idCardTypeName", "idCard",
                     "prescriptionNo", "orgCode", "orgName", "deptCode", "deptName",
                     "operatorId", "operationTime"],
        "map": dict(_common(activity=True), **{
            "PRESCRIPTION_NO": "prescriptionNo", "PRESCRIPTION_TYPE_CODE": "prescriptionTypeCode",
            "PRESCRIPTION_ISSUANCE_DATE": "prescriptionIssuanceDate",
            "PRESCRIPTION_VALIDITY_DAYS": "prescriptionValidityDays",
            "EXCUTE_DATE": "excuteDate",
            "PRESCRIPTION_ISSUANCE_ID": "prescriptionIssuanceId",
            "PRESCRIPTION_DISPENSING_ID": "prescriptionDispensingId",
            "PRESCRIPTION_NOTES": "prescriptionNotes",
        }),
        "watermark": "OPERATION_TIME", "overlap_days": 2, "full_days": 60,
    },
    "order_item": {
        "view": "ODS.V_INF_ORDER_ITEM", "endpoint": "emrOrderItem",
        "required": ["id", "orderId", "drugCode", "drugName", "operatorId", "operationTime"],
        "map": {
            "ID": "id", "ORDER_ID": "orderId", "DRUG_CODE": "drugCode", "DRUG_NAME": "drugName",
            "DRUG_SPECIFICATIONS": "drugSpecifications", "DRUG_DOSAGE_CODE": "drugDosageCode",
            "DRUG_DOSAGE_UNIT_CODE": "drugDosageUnitCode", "DRUG_DOSAGE_UNIT_NAME": "drugDosageUnitName",
            "DRUG_DOSAGE_TOTAL": "drugDosageTotal", "TCM_PRESCRIPTION": "tcmPrescription",
            "TCM_NUMBER": "tcmNumber", "TCM_DECOCTION_METHOD": "tcmDecoctionMethod",
            "TCM_USE_METHOD": "tcmUseMethod", "OPERATOR_ID": "operatorId", "OPERATION_TIME": "operationTime",
        },
        "watermark": "OPERATION_TIME", "overlap_days": 2, "full_days": 60, "depends_on": "order",
        "parent_fk": "orderId",   # P2 父受理门禁：子项发送前核对父（order.id=复合键）本进程受理证据
    },
    "vital_signs": {
        "view": "ODS.V_INF_VITAL_SIGNS_RECORD", "endpoint": "emrVitalSignsRecord",
        "required": ["id", "patientId", "activityTypeCode", "activityTypeName", "serialNumber",
                     "patientName", "idCardTypeCode", "idCardTypeName", "idCard",
                     "orgCode", "orgName", "deptCode", "deptName", "operatorId", "operationTime"],
        # 【2026-11-15 修正】平台实测 04"创建时间必填"——视图 v1.1 已把 CREATE_DATE 转标准日期串，恢复映射。
        "map": dict(_common(activity=True), **{
            "CREATE_DATE": "createDate",
            "VENTILATORUSED_CODE": "ventilatorusedCode", "VENTILATORUSED_NAME": "ventilatorusedName",
            "CRITICAL_CARE_CODE": "criticalCareCode", "CRITICAL_CARE_NAME": "criticalCareName",
        }),
        "watermark": "OPERATION_TIME", "overlap_days": 2, "full_days": 60,
    },
    "admission_info": {
        "view": "ODS.V_INF_ADMISSION_INFO", "endpoint": "emrAdmissionInfo",
        "required": ["id", "patientId", "serialNumber", "patientName",
                     "idCardTypeCode", "idCardTypeName", "idCard",
                     "deptCode", "deptName", "admissionDate",
                     "wmConfirmedDiagnosisCode", "wmConfirmedDiagnosisName", "confirmedDiagnosisDate",
                     "orgCode", "orgName", "operatorId", "operationTime"],
        # 2026-11-15 接管他方 FXHIS.V_EMR_ADMISSION_INFO（已停推）：嘉和 MR_CLASS='EMR09.00.01'
        # 首份驱动+真实正文（他方为'暂无此内容'占位）；id/serial 与他方历史口径一致（无重复）。
        # required 补确定诊断三字段（放量实测 4/10202 条无任何诊断被平台 04；源数据缺失不造假，
        # 客户端拦截转 skipped_invalid 尾差，待临床补诊断后自动入窗）。
        "map": dict(_common(activity=False), **{
            "BED_NO": "bedNo", "WARD_CODE": "wardCode", "WARD_NAME": "wardName",
            "ADMISSION_DATE": "admissionDate",
            "WM_INITAL_DIAGNOSIS_CODE": "wmInitalDiagnosisCode",
            "WM_INITAL_DIAGNOSIS_NAME": "wmInitalDiagnosisName",
            "WM_CONFIRMED_DIAGNOSIS_CODE": "wmConfirmedDiagnosisCode",
            "WM_CONFIRMED_DIAGNOSIS_NAME": "wmConfirmedDiagnosisName",
            "CONFIRMED_DIAGNOSIS_DATE": "confirmedDiagnosisDate",
            "CHIEF_COMPLAINT": "chiefComplaint", "PRESENT_ILLNESS_HIS": "presentIllnessHis",
            "PHYSICAL_EXAMINATION": "physicalExamination", "STUDIES_SUMMARY_RESULT": "studiesSummaryResult",
        }),
        "watermark": "OPERATION_TIME", "overlap_days": 2, "full_days": 60,
    },
    "discharge_info": {
        "view": "ODS.V_INF_DISCHARGE_INFO", "endpoint": "emrDischargeInfo",
        "required": ["id", "patientId", "serialNumber", "patientName",
                     "idCardTypeCode", "idCardTypeName", "idCard",
                     "admissionDate", "dischargeDate",
                     "admissionDiagnosisCode", "admissionDiagnosisName",
                     "orgCode", "orgName", "deptCode", "deptName",
                     "operatorId", "operationTime"],
        # 2026-11-15 接管他方（已停推）：嘉和 MR_CLASS='EMR12.00' 首份驱动+真实正文；
        # 入院诊断 TYPE=2/出院 TYPE=3 国标映射；op=文书时间（修订重推幂等）。
        "map": dict(_common(activity=False), **{
            "ADMISSION_DATE": "admissionDate", "DISCHARGE_DATE": "dischargeDate",
            "ADMISSION_DIAGNOSIS_CODE": "admissionDiagnosisCode",
            "ADMISSION_DIAGNOSIS_NAME": "admissionDiagnosisName",
            "DISCHARGE_DIAGNOSIS_CODE": "dischargeDiagnosisCode",
            "DISCHARGE_DIAGNOSIS_NAME": "dischargeDiagnosisName",
            "ADMISSION_DESC": "admissionDesc", "TREATMENT_DESC": "treatmentDesc",
            "DISEASE_PROGRESSION_CODE": "diseaseProgressionCode",
            "DISEASE_PROGRESSION_NAME": "diseaseProgressionName",
        }),
        "watermark": "OPERATION_TIME", "overlap_days": 2, "full_days": 60,
    },
}

# 开闸顺序：先小量后大量（§17 #7）。
# 2026-11-15：**13 表全量在推**（8 旧表+检验/检验项目/医嘱/医嘱条目+生命体征 v1.1——
# CREATE_DATE 转标准串、下限 20 位截断均 DBA 已部署；尾差全部转 client-validate 预检）。
ROLL_OUT_ORDER = ["death", "admission", "exam", "exam_item",
                  "first_course", "daily_course",
                  "ex_lab", "ex_lab_item", "order", "order_item", "vital_signs",
                  "admission_info", "discharge_info"]
