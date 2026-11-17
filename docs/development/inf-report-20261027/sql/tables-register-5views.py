# 传染病上报扩展 · tables.py 新增 5 表注册段（供合并到 implementation/deploy/inf_pusher/tables.py）
# 与既有 6 表同构：_common(activity=True) 提供公共映射；子表 depends_on 保证先主后子。
# 列名与 V_INF_* 视图一致（含 CRBSB 沿用的拼写截断列名 EEXAMINATION_SUBJECTIVE_DESC /
#   EXAMINATION_QUANTIFICA_* / EXCUTE_DATE / VENTILATORUSED_*，map 已按实际列名映射）。
# 追加到 TABLES 字典，并把 ROLL_OUT_ORDER 追加 ["ex_lab","ex_lab_item","order","order_item","vital_signs"]。

CRBSB_ADD = {
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
    },
    "vital_signs": {
        "view": "ODS.V_INF_VITAL_SIGNS_RECORD", "endpoint": "emrVitalSignsRecord",
        "required": ["id", "patientId", "activityTypeCode", "activityTypeName", "serialNumber",
                     "patientName", "idCardTypeCode", "idCardTypeName", "idCard",
                     "orgCode", "orgName", "deptCode", "deptName", "operatorId", "operationTime"],
        "map": dict(_common(activity=True), **{
            "CREATE_DATE": "createDate",
            "VENTILATORUSED_CODE": "ventilatorusedCode", "VENTILATORUSED_NAME": "ventilatorusedName",
            "CRITICAL_CARE_CODE": "criticalCareCode", "CRITICAL_CARE_NAME": "criticalCareName",
        }),
        "watermark": "OPERATION_TIME", "overlap_days": 2, "full_days": 60,
    },
}
