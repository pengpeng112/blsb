# 入院/出院记录接管 · tables.py 新增 2 表注册段（供合并，2026-11-15）
# 追加到 TABLES 字典 + ROLL_OUT_ORDER 追加 ["admission_info","discharge_info"]

ADM_DIS_ADD = {
    "admission_info": {
        "view": "ODS.V_INF_ADMISSION_INFO", "endpoint": "emrAdmissionInfo",
        "required": ["id", "patientId", "serialNumber", "patientName",
                     "idCardTypeCode", "idCardTypeName", "idCard",
                     "deptCode", "deptName", "admissionDate",
                     "orgCode", "orgName", "operatorId", "operationTime"],
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
