"""合成仓储夹具（无真实患者）。

- slides 与 设计交付v0.4/契约与示例/first-hospital.synthetic-fixture.json 保持一致
  （由 contract 测试比对），并扩展 F01—F20 场景所需的病例/申请/报告/患者/就诊行。
- 全部为合成值：条码900001、病理号9000001、患者P000x 等。
"""
from __future__ import annotations

# --- Pitaya_Slice（切片/玻片）与设计契约样例对齐 -------------------------------
SLIDES = [
    {"Id": 1, "qptmh": "900001", "PathologyID": "9000001", "lkh": "fixture-block-1",
     "qpzt": 0, "zpzt": 0, "tmdyzt": 0},
    {"Id": 2, "qptmh": "900002", "PathologyID": "9000001", "lkh": "fixture-block-1",
     "qpzt": 0, "zpzt": 0, "tmdyzt": 0},
    {"Id": 3, "qptmh": "900003", "PathologyID": "9000001", "lkh": "fixture-block-2",
     "qpzt": 0, "zpzt": 0, "tmdyzt": 0},
    {"Id": 4, "qptmh": "990000", "PathologyID": "9000002", "lkh": None,
     "qpzt": 0, "zpzt": 0, "tmdyzt": 0},
    {"Id": 5, "qptmh": "990000", "PathologyID": "9000003", "lkh": None,
     "qpzt": 0, "zpzt": 0, "tmdyzt": 0},
    {"Id": 6, "qptmh": "090001", "PathologyID": "0900001", "lkh": None,
     "qpzt": 0, "zpzt": 0, "tmdyzt": 0},
    # F18：QPIS 命名空间下同键行（不与 PITAYA 合并读取）
    {"Id": 8, "qptmh": "900001", "PathologyID": "8800001", "lkh": None,
     "qpzt": 0, "zpzt": 0, "tmdyzt": 0, "__namespace": "QPIS"},
    # 唯一命中 9000002 / 9000003 的玻片条码（F07/F08/F09/F11/F12）
    {"Id": 10, "qptmh": "900006", "PathologyID": "9000002", "lkh": None,
     "qpzt": 0, "zpzt": 0, "tmdyzt": 0},
    {"Id": 11, "qptmh": "900007", "PathologyID": "9000003", "lkh": None,
     "qpzt": 0, "zpzt": 0, "tmdyzt": 0},
    # F05：文件名提示病理号与PIS不一致用例（barcode 900005 命中 9000004）
    {"Id": 9, "qptmh": "900005", "PathologyID": "9000004", "lkh": None,
     "qpzt": 0, "zpzt": 0, "tmdyzt": 0},
]

# --- Pitaya 主表（病例）--------------------------------------------------------
CASES = [
    {  # F01/F06：一患者两住院，未确认 VISIT 桥接
        "PathologyID": "9000001", "ReqNoteID": "1000000001", "HisID": "H0001",
        "PatientID": "P0001", "InPatientID": "IN2026001", "OutPatientID": None,
        "IdentityCard": "110101195001011239", "PatientName": "合成患者甲",
        "Gender": "1", "BirthDate": "1950-01-01",
        "CreateTime": "2026-09-01T08:00:00", "SpecRecvTime": "2026-09-01T09:00:00",
        "ReportTime": "2026-09-03T10:00:00", "VerifyReportTime": "2026-09-03T10:30:00",
        "Rysj": "synthetic gross", "Gjsj": "synthetic microscopic", "Zdyj": "synthetic diagnosis",
        "Status": 0, "LastPathologyID": None, "FreezingID": None,
    },
    {  # F07：PIS 证件校验位通过但 HIS 冲突
        "PathologyID": "9000002", "ReqNoteID": "1000000002", "HisID": "H0002",
        "PatientID": "P0002", "InPatientID": "IN2026002", "OutPatientID": None,
        "IdentityCard": "110101196001015672", "PatientName": "合成患者乙",
        "Gender": "2", "BirthDate": "1960-01-01",
        "CreateTime": "2026-09-02T08:00:00", "SpecRecvTime": "2026-09-02T09:00:00",
        "ReportTime": "2026-09-04T10:00:00", "VerifyReportTime": "2026-09-04T10:30:00",
        "Rysj": "g", "Gjsj": "m", "Zdyj": "d", "Status": 0, "LastPathologyID": None,
        "FreezingID": None,
    },
    {  # F09/F11/F12：未来申请时间、类别'--'、机构码缺失（申请信息表行见 APPLICATIONS）
        "PathologyID": "9000003", "ReqNoteID": "1000000003", "HisID": "H0003",
        "PatientID": "P0003", "InPatientID": None, "OutPatientID": "OP2026003",
        "IdentityCard": None, "PatientName": "合成患者丙", "Gender": "1",
        "BirthDate": "1975-05-05",
        "CreateTime": "2026-09-03T08:00:00", "SpecRecvTime": "2026-09-03T09:00:00",
        "ReportTime": None, "VerifyReportTime": None,
        "Rysj": None, "Gjsj": None, "Zdyj": None, "Status": 0,
        "LastPathologyID": None, "FreezingID": "FZ-1",
    },
    {  # F05：文件名病理号提示不一致的目标病例
        "PathologyID": "9000004", "ReqNoteID": "1000000004", "HisID": "H0004",
        "PatientID": "P0004", "InPatientID": "IN2026004", "OutPatientID": None,
        "IdentityCard": "110101198001014329", "PatientName": "合成患者丁",
        "Gender": "1", "BirthDate": "1980-01-01",
        "CreateTime": "2026-09-04T08:00:00", "SpecRecvTime": "2026-09-04T09:00:00",
        "ReportTime": "2026-09-06T10:00:00", "VerifyReportTime": "2026-09-06T10:30:00",
        "Rysj": "g4", "Gjsj": "m4", "Zdyj": "d4", "Status": 0,
        "LastPathologyID": None, "FreezingID": None,
    },
    {  # F14/F16：分区盘点发现报告变更、作废与追加
        "PathologyID": "0900001", "ReqNoteID": "1000000005", "HisID": "H0005",
        "PatientID": "P0005", "InPatientID": "IN2026005", "OutPatientID": None,
        "IdentityCard": "110101199001011237", "PatientName": "合成患者戊",
        "Gender": "2", "BirthDate": "1990-01-01",
        "CreateTime": "2026-08-01T08:00:00", "SpecRecvTime": "2026-08-01T09:00:00",
        "ReportTime": "2026-08-03T10:00:00", "VerifyReportTime": "2026-08-03T10:30:00",
        "Rysj": "g5", "Gjsj": "m5", "Zdyj": "d5-v1", "Status": 0,
        "LastPathologyID": None, "FreezingID": None,
    },
]

# --- 患者检查申请信息表（集成表，双 schema PITAYA/QPIS；F18 不双读） ------------
APPLICATIONS = [
    {"申请号": "1000000001", "申请单类型": "住院", "病人类型": "住院", "检查类型": "常规病理",
     "检查项目名称": "常规病理", "申请日期": "2026-09-01T08:00:00", "状态": 512,
     "作废人": None, "作废原因": None, "作废时间": None, "医疗机构编码": "YLV21051",
     "删除标记": 0, "__namespace": "PITAYA"},
    {"申请号": "1000000002", "申请单类型": "住院", "病人类型": "住院", "检查类型": "常规病理",
     "检查项目名称": "常规病理", "申请日期": "2026-09-02T08:00:00", "状态": 512,
     "作废人": None, "作废原因": None, "作废时间": None, "医疗机构编码": "YLV21051",
     "删除标记": 0, "__namespace": "PITAYA"},
    {"申请号": "1000000003", "申请单类型": "门诊", "病人类型": "门诊", "检查类型": "--",
     "检查项目名称": "--", "申请日期": "2028-10-26T11:16:59", "状态": 512,
     "作废人": None, "作废原因": None, "作废时间": None, "医疗机构编码": "YLV21051",
     "删除标记": 0, "__namespace": "PITAYA"},
    {"申请号": "1000000004", "申请单类型": "住院", "病人类型": "住院", "检查类型": "常规病理",
     "检查项目名称": "常规病理", "申请日期": "2026-09-04T08:00:00", "状态": 512,
     "作废人": None, "作废原因": None, "作废时间": None, "医疗机构编码": "YLV21051",
     "删除标记": 0, "__namespace": "PITAYA"},
    {"申请号": "1000000005", "申请单类型": "住院", "病人类型": "住院", "检查类型": "常规病理",
     "检查项目名称": "常规病理", "申请日期": "2026-08-01T08:00:00", "状态": 512,
     "作废人": None, "作废原因": None, "作废时间": None, "医疗机构编码": "YLV21051",
     "删除标记": 0, "__namespace": "PITAYA"},
]

# --- 患者检查报告信息表（报告流水号=病理号样例；仅样例形态，不证明唯一） ---------
# 医疗机构编码 = 报告机构（S03 段7 索引取报告机构，非申请机构；合成值）
REPORTS = [
    {"报告流水号": "9000001", "申请号": "1000000001", "报告单类型": "病理",
     "病理诊断": "synthetic diagnosis 1", "检查所见": "", "审核日期": "2026-09-03T10:30:00",
     "医疗机构编码": "YLV21051",
     "图像报告URL": "http://pis.example.invalid/Report/pitaya/9000001",
     "作废标记": 0, "__namespace": "PITAYA"},
    {"报告流水号": "0900001", "申请号": "1000000005", "报告单类型": "病理",
     "病理诊断": "d5-v1", "检查所见": "", "审核日期": "2026-08-03T10:30:00",
     "医疗机构编码": "YLV21051",
     "图像报告URL": "http://pis.example.invalid/Report/pitaya/0900001",
     "作废标记": 0, "__namespace": "PITAYA"},
    # F16：追加报告（新流水号），历史冻结不变
    {"报告流水号": "0900001-A1", "申请号": "1000000005", "报告单类型": "病理",
     "病理诊断": "d5-addendum", "检查所见": "", "审核日期": "2026-09-10T10:30:00",
     "医疗机构编码": "YLV21051",
     "图像报告URL": None, "作废标记": 0, "__namespace": "PITAYA"},
]

# --- HIS MEDREC.PAT_MASTER_INDEX ------------------------------------------------
PATIENTS = [
    {"PATIENT_ID": "P0001", "ID_TYPE": None, "ID_NO": None,
     "NAME": "合成患者甲", "SEX": "1", "DATE_OF_BIRTH": "1950-01-01"},
    # F07：HIS 证件号与 PIS 冲突（不同号码）
    {"PATIENT_ID": "P0002", "ID_TYPE": "01", "ID_NO": "110101196001019999",
     "NAME": "合成患者乙", "SEX": "2", "DATE_OF_BIRTH": "1960-01-01"},
    # F08：ID_TYPE 脏值（'临床试验'），号码缺失
    {"PATIENT_ID": "P0003", "ID_TYPE": "临床试验", "ID_NO": None,
     "NAME": "合成患者丙", "SEX": "1", "DATE_OF_BIRTH": "1975-05-05"},
    {"PATIENT_ID": "P0004", "ID_TYPE": "01", "ID_NO": "110101198001014329",
     "NAME": "合成患者丁", "SEX": "1", "DATE_OF_BIRTH": "1980-01-01"},
    {"PATIENT_ID": "P0005", "ID_TYPE": "01", "ID_NO": "110101199001011237",
     "NAME": "合成患者戊", "SEX": "2", "DATE_OF_BIRTH": "1990-01-01"},
]

# --- HIS MEDREC.PAT_VISIT（复合键 PATIENT_ID+VISIT_ID）---------------------------
ENCOUNTERS = [
    # F06：同患者两次住院，无已确认桥接 → 就诊等待
    {"PATIENT_ID": "P0001", "VISIT_ID": "1", "INSURANCE_TYPE": "1"},
    {"PATIENT_ID": "P0001", "VISIT_ID": "2", "INSURANCE_TYPE": "1"},
    {"PATIENT_ID": "P0002", "VISIT_ID": "1", "INSURANCE_TYPE": "1"},
    {"PATIENT_ID": "P0004", "VISIT_ID": "1", "INSURANCE_TYPE": "1"},
    {"PATIENT_ID": "P0005", "VISIT_ID": "1", "INSURANCE_TYPE": "2"},
]

# --- V_PIS_IMAGE（仅JPG/URL，不是WSI，F17）---------------------------------------
V_PIS_IMAGE = [
    {"YYDM": "1237000049557032XQ", "EXAMID": "0900001", "SBLX": "显微镜",
     "CCFS": "FTP", "CCLJ": "ftp://pis.example.invalid/0900001/诊断报告_1_001.jpg",
     "YXBZ": 1},
]
