# 传染病前置软件上报 · 5 类文书字段映射（v0.3，已过平台元数据核对+活库实测）

日期：2026-10-27 ｜ 任务：INF-DESIGN → INF-VERIFY → INF-VIEW-VERIFY ｜ 状态：**视图 v0.3 待 DBA 部署；引用表列已过 sjzc 元数据核对（§8）与 ODS 活库实测（§9）**

规范来源：《国家传染病智能监测预警前置软件数据集成和API接口规范-20260324版》（`数据上报/4、传染病病历数据上报/`，267 页）。
目标接口：`http://前置机IP:端口/hclient/emr/receive/emrXxx`，POST JSON（camelCase），T+0（当日 24 点前）。

## 0. 全局约定

| 项 | 取值/来源 | 说明 |
|---|---|---|
| 视图库 | ODS <ods-host>:1521/orcl（数据中心） | 用户已确认 ODS 延迟满足 T+0；全部 6 张视图集中在此库 |
| 机构代码 org_code | 暂用 `'49557032X'` 占位 | 规范要求"前置软件部署申请时国家平台分配的机构ID"（9 位）。**待前置机信息到位后确认是否=本院机构代码**，不一致时只改视图常量 |
| 机构名称 org_name | `'山东省第二人民医院'` | 同三甲复审视图口径 |
| 患者标识 patient_id | `PAT_MASTER_INDEX.PATIENT_ID`（全院唯一） | 关联 emr_patient_info 的机构内唯一标识 |
| 住院号 serial_number | `PAT_VISIT.INP_NO` | 规范定义"住院就诊对象顺序号" |
| 身份证件类别 | 固定 `'01'/'居民身份证'` | 三甲死亡记录视图同口径；**新生儿无证场景（出生日期 8 位）待定** |
| 身份证号 | `PAT_MASTER_INDEX.ID_NO` | |
| 科室代码值域 | HIS 院内 `DEPT_CODE` 原值 | 规范要求落在本院 base_dept"院内科室代码"值域内——base_dept 后续由本院同步，故直接用院内代码即天然一致 |
| 操作人 operator_id | 各源表操作人/签名医生工号 | 必须落在 base_user"用户ID"值域内；base_user 的同步源（HIS employee vs 嘉和用户表）**待定**，两套工号若不统一需映射 |

**伴生依赖（本期用户拍板不做，实现时注意）**：规范质控要求"数据表关联一致性"（业务表↔emr_patient_info 按 patient_id、↔emr_activity_info 按就诊流水号、检查主子表关联）。当前范围=住院 5 类；emr_patient_info / emr_activity_info / base_user / base_dept 四张伴生表**未纳入本期**，推送联调时若前置机因关联缺失拒收，需回头补（决策留给联调阶段）。

## 1. 住院病案首页 emr_admission_record → `V_INF_ADMISSION_RECORD`

源：ODS `HIS.PAT_VISIT`(a) + `HIS.PAT_MASTER_INDEX`(b)；诊断另出（见下方"诊断来源"）。
参考三甲视图：`ODS.V_EMR_ICH`（D09/D10 标）+ `V_EMR_ICH_DIAGNOSIS`。

| 规范字段 | 必填 | 源 | 备注 |
|---|---|---|---|
| id | 必填 | org\|patient_id\|visit_id | 确定性复合主键 |
| patient_id | 必填 | a.PATIENT_ID | |
| serial_number | 必填 | a.INP_NO | ≤20 |
| patient_name | 必填 | b.NAME | |
| id_card_type_code/name | 必填 | 固定 01/居民身份证 | 新生儿待定 |
| id_card | 必填 | b.ID_NO | |
| health_card_no | 非必填 | 空 | |
| pay_method_code/name | 建议填 | a.CHARGE_TYPE→CV07.10.005 | 复用 V_EMR_ICH 的付费方式映射（职工10/居民20/自费70…） |
| admission_num | 建议填 | a.VISIT_ID | |
| reg_no(病案号) | 建议填 | b.INP_NO | |
| admission_date | **必填** | a.ADMISSION_DATE_TIME | |
| admission_dept_code/name | 建议填 | a.DEPT_ADMISSION_TO + dept_dict | |
| discharge_date | 出院必填 | a.DISCHARGE_DATE_TIME | 在院患者不出首页或推空？**待定：建议只在出院后推** |
| discharge_dept_code/name | 出院必填 | a.DEPT_DISCHARGE_FROM + dept_dict | |
| discharge_ward | 非必填 | 空 | |
| admission_days | 出院必填 | discharge-admission+1 | |
| wm_outpatient_diagnosis_code/name | **必填** | 诊断源 DIAGNOSIS_TYPE=1 | MEDREC.DIAGNOSIS 值域（1门急诊/2入院/3出院/7损伤/8病理，平台148号已确认）。ODS 若无 MEDREC 镜像则从嘉和首页诊断取——**待核对 ODS 覆盖** |
| discharge_diagnosis_code/name | 出院必填 | 诊断源 DIAGNOSIS_TYPE=3 主诊断 | 同上 |
| wm_other_diagnosis_code/name | 非必填 | DIAGNOSIS_TYPE=3 非主+7/8 | 逗号拼接 |
| external_causes_code/name/system | 非必填 | DIAGNOSIS_TYPE=7 | |
| pathological_diagnosis_code/name/no | 非必填 | DIAGNOSIS_TYPE=8 + 病理号 | |
| allergy_code | 建议填 | a.ALERGY_DRUGS 判定 | 0无1有；判定词表（'无'/'否认'等）抽样后收紧 |
| allergy_drug | 过敏时必填 | a.ALERGY_DRUGS | 实列已核对 |
| autopsy_code | 非必填 | 暂空 | AUTOPSY_INDICATOR 实列存在但值域未确认，不猜 |
| chief_physician_id / resident_physician_id | 建议填 | a.ATTENDING_DOCTOR / a.DOCTOR_IN_CHARGE | 已核对实列（§8） |
| accept_org_code/name | 非必填 | 空 | 转院场景 |
| org_code/name、dept_code/name | 必填 | 常量 + 出院科室 | dept 建议用出院科室 |
| operator_id / operation_time | 必填 | a.ATTENDING_DOCTOR + 出院时间 | PAT_VISIT 无 OPERATOR 列（§8），操作人=主治医师 |

诊断来源（v0.2 已定）：**HIS.DIAGNOSIS**（ODS 镜像，三甲 V_EMR_ICH_DIAGNOSIS 同源），DIAGNOSIS_TYPE：1门急诊/2入院/3出院/7损伤/8病理；编码经 `CDA.CDA_DICTIONARY`（字典名称='ICD-10诊断编码'，系统标识='HIS'）做院标→国标 ICD10 映射，映射不到回退院标原码。

## 2. 住院首次病程记录 emr_first_course → `V_INF_FIRST_COURSE`

源：ODS `JHEMR.JHMR_FILE_INDEX` + `JHEMR.JHMR_FILE_CONTENT_TEXT`（CLOB 纯文本）。
参考三甲模式：`V_EMR_HOS_POSTOPER_RECORD_JHEMR`（TOPIC 匹配 + delete_flag=0 + pat_visit 时间窗）。**三甲清单没有"住院首次病程记录"视图，本视图新写。**

| 规范字段 | 必填 | 源 | 备注 |
|---|---|---|---|
| id | 必填 | org\|patient_id\|visit_id\|file_unique_id | |
| patient_id / serial_number / patient_name / 证件三件套 | 必填 | 关联 his.pat_visit/pat_master_index | 同全局 |
| ward_name/ward_no/bed_no | 建议填 | jhmr_file_index.DEPT_CODE + FILE_NO(床号) | 三甲术后首程同口径（FILE_NO→SICKBED_NUM） |
| create_time(记录时间) | 建议填 | t.CREATE_DATE_TIME | |
| chief_complaint(主诉) | 建议填 | 正文 CLOB 截取或空 | **结构化拆分暂不做**（见下） |
| present_illness_his(病例特点) | 建议填 | 正文整段放 course 类字段 or 空 | |
| diagnosis_basis(诊断依据) | 建议填 | 同上 | |
| wm_inital_diagnosis_code/name | 建议填 | 空 或 关联嘉和 diagnosis | 初步诊断结构化**二期增强** |
| 中医字段 tcm_* | 非必填 | 空 | 西医医院 |
| wm_diff_diagnosis_* | 建议填 | 空 | |
| treatment_plan | 非必填 | 空 | |
| resident_physician_id | 建议填 | t.CREATOR_ID | 须在 base_user 值域 |
| org/dept(填报科室 t.DEPT_CODE)/operator/operation_time | 必填 | 常量+t.DEPT_CODE+t.LAST_MODIFY_USER_ID | |

TOPIC 过滤：`TOPIC LIKE '%首次病程记录%' AND TOPIC NOT LIKE '%术后%'`（排除术后首次病程记录）。
**正文结构化（病例特点/诊断依据/初步诊断分列）二期选项**：CDA 区 `TT_ESBHL_FIRST_COU_ROD`（CASES_CHARACTERISTIC/DIAG_BASIS/PRNA_DIAG_WEST/TREAT_PLAN）已结构化，但该区新鲜度未核验（用户暂缓）；嘉和 XML 节点 `JHMR_FILE_CONTENT_DG`（DG_CODE+DG_PLAIN_CONTENT）可按节点取。两者均列为二期增强，本期正文整段进 `present_illness_his` 满足"建议填"。

## 3. 住院日常病程记录 emr_daily_course → `V_INF_DAILY_COURSE`

源：同上（jhmr_file_index + content_text）。正文 course/order_content 均为**建议填**，纯文本方案完全够用。

| 规范字段 | 必填 | 源 | 备注 |
|---|---|---|---|
| 标识/患者/证件/病区床号 | 同上 | | |
| create_time | 建议填 | CREATE_DATE_TIME | |
| course(住院病程) | 建议填 | MR_CONTENT 截 2000 字（GBK 4000字节上限，§11） | 规范 text 无长度限制，推送器注意报文体积 |
| order_content(医嘱内容) | 建议填 | 空（或二期关联医嘱） | |
| observation_result / treatment | 非必填 | 空 | 中医 |
| disease_progression_code/name | 建议填 | 空 | WS/T364.11 转归代码 |
| org/dept/operator/operation_time | 必填 | 同上 | |

TOPIC 过滤：`TOPIC LIKE '%病程记录%' AND TOPIC NOT LIKE '%首次%' AND TOPIC NOT LIKE '%术后%'`（日常病程在嘉和文书名通常为"日常病程记录/病程记录"，**真实 TOPIC 分布需抽样核验后收紧**）。

## 4. 死亡信息 emr_death_info → `V_INF_DEATH_INFO`（**v0.6，2026-11-06 已部署**）

源（v0.6 双驱动，[SQL](sql/04-v_inf_death_info_v0.6.sql)）：嘉和死亡文书 `JHEMR.JHMR_FILE_INDEX`（TOPIC∈{死亡记录,24小时内入院死亡记录,死亡记录模板%}，**死亡病例讨论结论记录不作驱动**，每就诊取首份）∪ `HIS.PAT_VISIT`（离院方式=5 兜历史与无文书病例），按 ID=患者ID_次数 去重、文书行优先。背景：病案编码补填滞后数天、CDA 死亡馈送 2024-08-30 起停更——v0.5 单驱动 T+0 不可达（平台质控"死亡 0 人"根因）。

| 规范字段 | 必填 | 源 | 备注 |
|---|---|---|---|
| id | 必填 | patient_id\|visit_id | 口径未变（铁律 4：其余五表不受影响） |
| activity_type_code/name | 必填 | 固定 6/住院 | 诊疗活动类型代码表（1门诊…6住院） |
| serial_number 等 | 必填 | pat_master_index.INP_NO‖visit_id | crbsb 口径未变 |
| treatment_desc | 建议填 | CDA.TREAT_PROC_DESCR | CDA 停更实际恒空，链位保留 |
| dead_date | **必填** | NVL(CDA.DEATH_DATE, NVL(离院时间, 文书CREATE_DATE_TIME)) | PAT_VISIT.DEATH_DATE_TIME 源端不填（平台已知陷阱，不用）；v0.6 第三链=文书创建时间（离院未办的瞬时窗口近似） |
| direct_cause_code/name | **必填** | NVL(CDA.DIRECT_DEATH_REASON_CODE/NAME, 出院主诊断国标映射) | 兜底=TYPE3 主诊断经 CDA_DICTIONARY 映射（近 90 天实测命中 88/88） |
| death_diagnosis_code/name | 传染病时必填 | NVL(CDA.DEATH_DIAG_CODE/NAME, **出院主诊断国标映射**) | **v0.6 变更：新增主诊断映射兜底**（同 direct_cause 的 fallback）——CDA 停更致恒空被平台质控点名；实测填充率 341/342=99.7% |
| chief_physician_id / operator_id | 必填 | 文书行=CREATOR_ID，兜底 ATTENDING_DOCTOR | **v0.6 变更**：写死亡记录的医生即操作人；PAT_VISIT 行仍=主治医师 |
| org/dept(死亡科室) | 必填 | 文书行=NVL(在院科室 INFECT 映射, NVL(出院科室 INFECT 映射, 在院科室原码))；PAT_VISIT 行=出院科室 INFECT 映射+原码兜底 | **v0.6 变更**：死亡当天多未办离院，文书在院科室优先；DEPT_CODE 输出=平台传染病科室字典码（412 HIS 码→74 平台码，如 A28=重症医学科、A03.01=呼吸内科专业），与五表同口径，base_dept 值域即该字典 |
| operation_time（水位） | 必填 | 文书行=LAST_MODIFY_DATE_TIME；PAT_VISIT 行=离院时间 | **v0.6 变更**：文书修订推新 op → 幂等更新重推（前置机按 ID 更新，设计内）；已上线就诊的已发 op（=当年离院时间）早于文书 op 属预期，重推即更新 |

## 5. 检查报告 emr_ex_clinical → `V_INF_EX_CLINICAL`（+ 项目子表）

源：ODS `HIS.EXAM_MASTER`(m) + `HIS.EXAM_REPORT`(r) + `HIS.EXAM_ITEMS`(i)。参考三甲 `V_EMR_EXAM_IMAGE_REP`（HIS 源库 HISUSER 版，本方案改用 ODS 镜像同表）。

| 规范字段 | 必填 | 源 | 备注 |
|---|---|---|---|
| id | 必填 | org\|EXAM_NO | |
| patient_id | 必填 | m.PATIENT_ID | 门诊检查 PATIENT_ID 亦全院唯一 |
| activity_type_code/name | **必填** | m.PATIENT_SOURCE 或 SRC 类型→1门诊/2急诊/6住院 | 就诊记录类型（住院范围=m.PATIENT_SOURCE='I'→6住院）；EXAM_CLASS 存中文（已知陷阱） |
| serial_number | 必填 | 住院=m 关联 pat_visit.INP_NO；门诊=门诊号 | 本期住院 5 类，门诊检查是否推**待定** |
| patient_name/证件 | 必填 | m/pat_master_index | |
| ward/bed | 住院建议填 | 空 | |
| application_form_no | 建议填 | m.EXAM_NO（申请单号同检查号）或 ORDER_NO | |
| apply_org/apply_dept | apply_dept **必填** | m.REQ_DEPT + dept_dict | 申请科室 |
| symptom/treatment_desc/special | 非必填 | m.CLIN_SYMP / 空 | |
| examination_type_code/name | **必填** | EXAM_CLASS→检查类别代码：10 影像学/11 病理/99 其它 | 值域仅 3 值。CT/MR/X线/超声→10；**内镜/心电归 10 还是 99 待确认**（三甲细分映射不适用于此值域） |
| examination_objective_desc | 必填(可后补) | r.DESCRIPTION | 所见 |
| examination_subjective_desc | 必填(可后补) | r.IMPRESSION | 提示 |
| examination_notes | 有则必填 | r.RECOMMENDATION 或 MEMO | |
| examination_report_no | 必填(可后补) | m.EXAM_NO | |
| examination_report_date | 有报告必填 | r.REPORT_TIME | |
| examination_report_id(报告医师) | 有报告必填 | r.REPORTER | 须在 base_user |
| org/dept(报告科室 m.EXAM_SUBDEPT?)/operator | 必填 | | 报告科室取 r 侧执行科室**待核对** |
| 增量水位 | — | r.REPORT_DATE_TIME / m 确认时间 | T+0 按报告时间窗取 |

项目子表 `V_INF_EX_CLINICAL_ITEM`：id=org|EXAM_NO|EXAM_ITEM_NO，ex_clinical_id=org|EXAM_NO，item_code/name=`i.EXAM_ITEM_CODE/EXAM_ITEM`（**EXAM_ITEM_CODE 常空需抽样核验**；空时用 EXAM_ITEM 名称占位并标注），定量结果/单位空，operator/operation_time 同主表。

## 6. 值域清单（规范附录已核）

- 检查类别代码：10 影像学检查 / 11 病理学检查 / 99 其它（附录第 7 项）
- 诊疗活动类型代码：1门诊 2急诊 3留观入观 4留观出观 5入院 6住院 7首次病程 8日常病程 9出院（附录第 25 项）
- 身份证件类别：WS364.3-2023 CV02.01.101（01 居民身份证…）
- 病情转归：WS/T364.11-2023 CV05.10.010
- 中医类：GB/T15657-2021 / GB/T16751.3-2023（本期不用）
- 完整值域表在规范第 5 章附录一（P150-236），推送器侧代码↔名称强一致校验

## 7. 待办与风险（联调前必须闭环）

1. **前置机外部信息**：IP/端口、国家分配机构 ID（org_code 是否=49557032X）、license——用户提供后填视图常量与推送配置。
2. **抽样核验**（值域/落点，见 §8 已核清单之外的剩余项）：① EXAM_ITEM_CODE 非空率；② CDA 死亡记录表数据是否在更新（死亡兜底口径已不依赖它，仅影响死因精度）；③ 首次/日常病程 TOPIC 真实分布；④ PATIENT_SOURCE='I' 值域；⑤ ALERGY_DRUGS 判定词表；⑥ AUTOPSY_INDICATOR 值域。
3. **伴生表**（patient_info/activity_info/base_user/base_dept）本期不做，联调若拒收再补。
4. 检查报告门诊部分是否推送：本期按住院 PATIENT_SOURCE='I' 过滤，门诊待用户定。
5. 视图命名 `V_INF_*` 与三甲 `V_EMR_*` 区分；部署走 DBA 受控流程（参考 `docs/deployment/his-ddl-clock-20261022/sql/` 分阶段结构），**本目录 SQL 均为本机草稿未部署**。

## 8. 平台元数据核对记录（2026-10-27，INF-VERIFY-20261027，sjzc 通道）

草稿引用的每个表/列已对 DATA_CENTER(ODS 8.216) 平台元数据逐项核对，结论：

| 核对对象 | 结果 | 对视图的影响 |
|---|---|---|
| HIS.PAT_VISIT 关键列 | ✅ PATIENT_ID/VISIT_ID/INP_NO/CHARGE_TYPE/ADMISSION_DATE_TIME/DEPT_ADMISSION_TO/DISCHARGE_DATE_TIME/DEPT_DISCHARGE_FROM/DISCHARGE_DISPOSITION/DOCTOR_IN_CHARGE/ALERGY_DRUGS/PATHOLOGY_NO/DEATH_REASON 均存在 | — |
| PAT_VISIT 医师/操作人列 | ❌ 草稿 v0.1 的 `ATTENDING_PHYSICIAN`、`OPERATOR` **不存在**；实列为 `ATTENDING_DOCTOR`（主治）/`DIRECTOR`/`CHIEF_DOCTOR`，无 OPERATOR | v0.2 已改：主治=ATTENDING_DOCTOR，操作人=ATTENDING_DOCTOR |
| HIS.PAT_MASTER_INDEX | ✅ PATIENT_ID/INP_NO/NAME/SEX/DATE_OF_BIRTH/ID_NO 存在 | — |
| HIS.DEPT_DICT | ✅ ODS 有镜像（DEPT_CODE VARCHAR2(8) 必填/DEPT_NAME） | — |
| 诊断源 | 三甲 V_EMR_ICH_DIAGNOSIS 实际用 **HIS.DIAGNOSIS**（ODS 镜像，23 列：DIAGNOSIS_TYPE VARCHAR2(1)、DIAGNOSIS_NO NUMBER、DIAGNOSIS_CODE VARCHAR2(30)、DIAGNOSIS_DESC VARCHAR2(240)），TYPE 1/2/3/7/8 语义=148 号口径 | v0.2 已从 JHEMR.DIAGNOSIS 切换到 HIS.DIAGNOSIS |
| ICD 院标→国标映射 | ✅ `CDA.CDA_DICTIONARY`（中文列名：字典名称/院标编码/院标名称/国标编码/国标名称/系统标识），三甲死亡视图同用法（字典名称='ICD-10诊断编码' AND 系统标识='HIS'） | v0.2 诊断/死因编码均加国标映射 |
| JHEMR.JHMR_FILE_INDEX（ODS 镜像） | ✅ PATIENT_ID/VISIT_ID/FILE_NO/TOPIC/CREATOR_ID/CREATE_DATE_TIME/LAST_MODIFY_DATE_TIME/LAST_MODIFY_USER_ID/DEPT_CODE/FILE_UNIQUE_ID/DELETE_FLAG 全存在 | 病程两视图无需改 |
| JHEMR.JHMR_FILE_CONTENT_TEXT | ✅ FILE_UNIQUE_ID/MR_CONTENT(CLOB) | — |
| CDA.TT_ESBHL_DEATH_ROD 全 43 列 | ✅ 含 DEATH_DATE、**DIRECT_DEATH_REASON_CODE/NAME（直接死因）**、DEATH_DIAG_CODE/NAME（死亡诊断）、TREAT_PROC_DESCR（诊疗过程）；无 PATIENT_ID，有 PAT_ID_NUMBER(身份证) | v0.2 死亡视图改按 身份证+VISIT_ID 关联，死因/死亡诊断/诊疗过程取实列，兜底=三甲口径（出院时间+出院主诊断国标映射） |
| HIS.EXAM_MASTER（ODS 镜像 58 列） | ✅ 草稿引用列全存在（REQ_DEPT/CLIN_SYMP/EXAM_CLASS/EXAM_SUBDEPT/CONFIRM_DOCT/REPORT_DATE_TIME/REQ_DATE_TIME/PATIENT_SOURCE/VISIT_ID） | 检查两视图无需改 |
| HIS.EXAM_REPORT（ODS 镜像） | ✅ EXAM_NO/DESCRIPTION/IMPRESSION/RECOMMENDATION/REPORTER/REPORT_TIME 存在（客观所见/主观提示/备注/报告医师/报告时间一一对应） | — |
| HIS.EXAM_ITEMS（ODS 镜像） | ✅ EXAM_NO/EXAM_ITEM_NO/EXAM_ITEM/EXAM_ITEM_CODE 存在 | — |
| 附：HIS.STAFF_DICT | ✅ 存在（EMP_NO/NAME/TITLE/…），base_user 后续同步可用 | 备用 |

**给建视图人（DBA）的执行提示**：6 张视图按编号顺序执行即可，互相无依赖；全部 `WITH READ ONLY`；只引用 ODS 既有账号可见对象（三甲 V_EMR_* 视图同库同权限已验证可行）。执行前替换两处占位：① `2026-01-01` 起始窗口；② `49557032X`（若国家分配机构 ID 不同）。建完按 his-ddl-clock 的 verify 清单抽样核对行数与关键列非空率。

## 9. 活库实测记录（2026-10-27 晚，INF-VIEW-VERIFY-20261027，ODS live 限量只读）

用户在 ODS 建 5 张视图（20:57）后经 <relay-jump> 连接器实测（约 15 条聚合 SELECT，零写入）：

**部署状态与问题**

| 视图 | 状态 | 根因 | 处置 |
|---|---|---|---|
| V_INF_DEATH_INFO | VALID，数据健康 | — | 需按 v0.3 重建：SERIAL_NUMBER 取的 PAT_VISIT.INP_NO **实测全空** |
| V_INF_EX_CLINICAL / _ITEM | VALID 但 **0 行** | `PATIENT_SOURCE='I'` 假设错误：实测值域是 **NULL/'1'(门诊)/'2'(住院)**，且 30 天内 10675 行 NULL 中还混有 6236 例住院 | v0.3 改为 JOIN PAT_VISIT 命中=住院；并加"已有报告才推"过滤（实测 21% 申请未出报告） |
| V_INF_FIRST_COURSE / V_INF_DAILY_COURSE | **INVALID（ORA-00942）** | ODS 属主缺 JHEMR 表直接授权（连接账号可查走的是角色；**ODS 下此前无任何嘉和类视图先例**） | DBA：`GRANT SELECT ON JHEMR.JHMR_FILE_INDEX TO ODS; GRANT SELECT ON JHEMR.JHMR_FILE_CONTENT_TEXT TO ODS;` 后 `ALTER VIEW ... COMPILE;`（同时按 v0.3 重建修 SERIAL_NUMBER） |
| V_INF_ADMISSION_RECORD | **未建成**（ODS 无任何同名/改名对象） | 真因不是字段过长，是 **老 Oracle(30字符上限，推断11g) 不支持外层列关联穿透聚合内联视图**（LISTAGG 双层嵌套报 ORA-00904 A.VISIT_ID，已复现）；字段过长实际发生在项目子表 EXAMINATION_QUANTIFICATION_UNIT(31字符) | v0.3 已改单层关联+字典预聚合写法（实测通过）；用户改的 `_Ut` 截断列在 v0.3 中规范为 `EXAM_QUANT_UNIT` |

**数据实测结论（近期窗口聚合，无患者明细出库）**

- 出院（病案首页口径）：近 14 天 2634 例；身份证 2630/2634（4 例缺——新生儿/未登记，规范必填需补策略）；主治医师 1775/2634（67%）；门急诊诊断(TYPE=1) 199/200；**PAT_VISIT.INP_NO 全空、PAT_MASTER_INDEX.INP_NO 100% 填充**（住院号统一改 `NVL(pv.INP_NO, pmi.INP_NO)`）。
- 死亡：2026 年以来 319 例；死亡时间/直接死因/科室 100% 非空（兜底口径生效）；**CDA.TT_ESBHL_DEATH_ROD 零命中**（死亡诊断/诊疗过程描述全空——该表无数据或键不匹配，死亡诊断待传染病判定规则补）。
- 检查：数据新鲜（最新 2026-10-27 20:42）；修正后住院检查近 7 天 7315 例（住院号/申请科室 100%），已有报告 5800（客观所见 98.4%/主观 97.8%/报告医师与时间 100%）；项目子表 5947 行**编码与名称 100% 真实填充**（EXAM_ITEM_CODE 有值，非名称占位）。
- ICD 映射字典 CDA.CDA_DICTIONARY（ICD-10诊断编码×HIS）41978 条，资源充足。
- 病程 TOPIC 近 7 天：首次病程记录 1332、日常病程记录 1214、术后首次 563（被排除）、泛化"病程记录"120 及查房/输血/危急值等少量变体——排除法过滤验证有效，变体并入日常病程口径。

**下一步 DBA 一次到位清单**：① 两条 GRANT；② 用 v0.3 文件 CREATE OR REPLACE 全部 6 张（01 新建 + 02/03/04/05/06 重建，含住院号兜底/住院判定/报告过滤/列名修正）；③ 建后复核：6 视图 STATUS=VALID、各近 7 天行数与上表量级一致、SERIAL_NUMBER 非空率 100%。

## 10. 二轮复核记录（2026-10-27 21:23 部署后，INF-VIEW-RECHECK-20261027）

DBA 按清单授权+重建 6 张（21:23），复核结果（~15 条聚合 SELECT，零写入）：

| 视图 | 状态 | 数据 |
|---|---|---|
| V_INF_DEATH_INFO | ✅ VALID | 319 例，住院号/死亡时间/死因/科室 100%——**全绿** |
| V_INF_EX_CLINICAL | ✅ VALID | 7 天 6081 例：住院号/申请科室/报告时间 100%、客观所见 98.4%、主观 97.9%——**全绿** |
| V_INF_EX_CLINICAL_ITEM | ✅ VALID | 7 天 5947 行：编码/名称/主表外键 100%——**全绿** |
| V_INF_ADMISSION_RECORD | ⚠️ VALID 但需按 v0.4 重建 | 7 天 1396 例：住院号 100%、出院诊断 99.8%；**门急诊诊断（规范必填）仅 40%**——TYPE=1 行存在但诊断编码仅 47% 填充（名称 100%）。v0.4 加两级兜底：编码按名称查映射字典 + 患者级全空用 TYPE=2 入院诊断，实测覆盖 99.8%（500/500 抽样验证通过） |
| V_INF_FIRST_COURSE / V_INF_DAILY_COURSE | ❌ 仍 INVALID（ORA-00942） | **根因是本人 v0.3 文件笔误：JOIN 行 `JHMR.JHMR_FILE_CONTENT_TEXT` 少写 E**（JHEMR），DBA 忠实执行；授权实际已到位（DBA_TAB_PRIVS 证实 ODS 已有两表 SELECT）。文件已修正为 JHEMR，待重跑 02/03 |

**剩余动作（DBA）**：重跑 `01（v0.4，门急诊诊断兜底）、02、03（JHEMR 笔误修正）` 三份 SQL 即可；04/05/06 无需动。
建后复核口径：6 张 VALID；01 门急诊诊断非空率 ≈99.8%；02/03 近 7 天行数约 1300/1300 量级、住院号 100%。

## 11. 三轮复核（21:35 重跑 01/02/03 后，INF-VIEW-FINAL-20261027）

| 视图 | 结果 |
|---|---|
| 全部 6 张 | ✅ STATUS=VALID |
| V_INF_ADMISSION_RECORD(v0.4) | ✅ 7天1396例：住院号100%、**门急诊诊断 1393/1396=99.8%**（兜底链生效）、出院诊断99.8%、科室100% |
| V_INF_FIRST_COURSE | ⚠️ 行数/住院号/科室 100%（7天1131），但**正文列 3900 字触发 ORA-06502**（GBK 库 SQL 层 VARCHAR2 4000 字节上限，中文2字节/字；日常病程碰巧都短未触发，三甲同款视图用 2000 正是此因） |
| V_INF_DAILY_COURSE | ✅ 7天1341：住院号/科室/正文 100% |
| 其余三张 | 未动，维持 §10 全绿 |

**修正**：02/03 正文截取 3900→**2000 字**（GBK 4000 字节上限；实测全周 1132 篇 100% 可取，15.5% 触顶截断）。全文推送列为二期（推送器直读 CLOB 或分段）。
**剩余动作**：已完成（21:45 重跑 02/03）。

## 12. 收官确认（21:45 后复核，INF-VIEW-DONE-20261027）

6 张视图全部 VALID 且数据核验通过：首次病程 7天1131（住院号/正文/科室 100%）、日常病程 1341（100%）。
**视图层完工。** 后续待前置机 IP/端口/机构 ID（org_code 常量替换）+ 推送插件开发（复用 drug_trace 骨架）。

## 13. 伴生表与 serial_number 实证（2026-10-28，INF-COMPANION-20261028，用户三项拍板后执行）

**用户拍板（2026-10-28）**：①伴生表同意补做；②serial_number 按"查 ODS/HISUSER 已有同名视图实证"；③死亡口径选 2.2.14 节=**全量死亡**（现 V_INF_DEATH_INFO 即此口径，无改动）。

### 关键发现：HIS 源库已存在同接口实现（FXHIS schema，2026-04-23 起）

按用户提示排查 HIS 源库（<his-host>），`FXHIS` schema 内已有**按传染病前置软件规范原样命名**的视图群：

| 已有对象 | 覆盖规范表 | 实测状态 |
|---|---|---|
| FXHIS.V_EMR_PATIENT_INFO | emr_patient_info（实时） | 基于活表（MEDREC.PMI 等） |
| FXHIS.V_EMR_ACTIVITY_INFO | emr_activity_info（实时） | 47708 行，OPERATION_TIME 至 2026-10-28（门诊34695/住院10988/急诊2025） |
| FXHIS.V_EMR_ADMISSION_INFO / V_EMR_DISCHARGE_INFO / V_EMR_VITAL_SIGNS_RECORD | 入院记录/出院记录/生命体征 | 已建 |
| FXHIS.V_SYS_USER_DEPT_INFO | base_user+base_dept 合并口径 | **196/196 覆盖我方 6 视图操作人工号**（近30天主治医师实测） |
| COMM.INFECT_DEPT_DICT | base_dept 的 target 映射 | 412 行 HIS_DEPT_CODE→INFECT_DEPT_CODE 四列字典 |

**结论一（改决策）**：伴生表/基础表**不再自建**——FXHIS 已实现且与我们的工号/科室口径兼容（联调时确认其推送器是否在跑：FXHIS.EMR_INTERFACE_LOG 为空表，视图可能"建好待用"，需向信息科确认归属与状态）。我方 6 视图 operator/dept 值域天然落在其 base_user/base_dept 内。

**结论二（serial_number 契约实证）**：FXHIS.V_EMR_ACTIVITY_INFO 住院行（10988）SERIAL_NUMBER 为**唯一 8 位数字**（长度8:10103/9:879/10:5），实测**≠病案号(PMI.INP_NO)、≠患者ID、≠PAT_VISIT.INP_NO/INP_SERIAL_NO、≠MR_PAT_VISIT**（患者ID 100% 属 MEDREC 体系，排除关联失败）。其号源在该视图 12KB 文本内（无 DBMS_METADATA 权限导不全）。**我方 5 类视图 serial 必须与他们对齐，否则前置机关联一致性断裂**。行动：请 DBA 导出 FXHIS.V_EMR_ACTIVITY_INFO 全文（或问信息科作者），一步定位 SERIAL_NUMBER 表达式后统改 6 视图。

**结论二·后续破案（2026-11-10，INF-LINKAGE-FIX）**：FXHIS.V_EMR_ACTIVITY_INFO 视图全文取回（HIS 源库 <his-host>）——
**住院 serial=`medrec.PAT_MASTER_INDEX.INP_NO‖VISIT_ID`，与我方六视图完全一致，"待对齐"问题不存在**（本节上方"必须对齐"的担忧就此关闭）；
真因=他方活动视图滚动窗（出院>SYSDATE-60/在院>-20/门诊>-20）vs 我方 2026 全量回填。整改=方案 A 镜像关联（六视图 v0.7 JOIN 锚点镜像，见 sql/MIRROR-fxhis-关联镜像建设说明.sql）。

**结论三（CDA 判死刑）**：CDA 共享文档区病程表最后更新 2024-08-31（停更 2 年+），历史 INHOS_NO 亦非病案号——不再作为任何口径参考；唯一残值 CDA_DICTIONARY（ICD 映射）继续用并加命中率监控。

**结论四（用户体系统一）**：嘉和文书作者 ID 与 HIS 工号 100% 同源（近30天 3000/3000 命中 STAFF_DICT）——operator_id 全线统一 HIS 工号口径成立。

## 14. 前置机端点联通与上传测试（2026-10-28，INF-ENDPOINT-TEST-20261028）

**端点**：`http://<frontend-host>/hclient/emr/receive/emrExClinical`（用户提供）。开放端口：8881/8882/9000。

| 测试 | 来源 | 结果 |
|---|---|---|
| 本机 Windows 直连 | 工作站 | 超时（本机不在该内网段，与连库路径一致，预期内） |
| GET 端点 | <relay-jump> 平台服务器 | **HTTP 405**（"此URL不支持GET"，服务签名 Hclient=前置软件本体，响应 8ms） |
| POST {} 空报文 | <relay-jump> | `{"result":false,"errorCode":"04","desc":"主键ID必填！,患者ID必填,诊断活动类型代码必填,…,操作时间必填"}`——**传输/JSON/校验层全通**，且响应实锤 emrExClinical 必填集与 camelCase 字段名 |
| POST 合成完整记录（假患者"联调测试"+校验位合法假身份证+TEST 前缀各 ID+科室 020302） | <relay-jump> | 字段校验**全部通过**，止于 `"医疗机构编码不属于配置范围内！"`（errorCode 04）——**orgCode 49557032X 未在前置机登记** |
| <prod-host> worker 宿主 | 本机 | SSH 22 拒绝（访问走部署文档路径未验证；与 <frontend-host> 同段，<relay-jump> 实测 0.25ms 可达，连通概率极高，部署时复验） |

**结论**：内网链路与上传能力**全链路验证通过**（传输→JSON→必填校验→机构校验逐层到达）；测试记录被拒未入库，无需清理。
**唯一剩余配置项**：向前置机登记我方机构编码——两条路二选一：①向疾控/部署申请材料取**国家平台分配的机构 ID**（若≠49557032X 需同步改 6 张视图常量）；②请前置机管理员把 49557032X 加入配置。登记后重跑本测试可继续探测下一层校验（科室/操作人值域即 base 表）。
测试报文与响应原文存 `review/` 同级 `evidence/endpoint-test-20261028/`。

## 15. crbsb 对齐 + 端点上传成功 + emr_order 审查（2026-10-28，INF-ALIGN-CRBSB-20261028）

### 15.1 端点上传成功（§14 的阻断解除）

用 crbsb 视图口径 orgCode=**370104000** 重发合成测试记录 → `{"result":true,"desc":"操作成功"}` 入库，随后 DELETE
同 id 成功清理。**POST/DELETE 双向实测通过；370104000 即前置机登记的机构编码**（49557032X 被拒）。另实测：dept 用
HIS 原码、operator 用未登记 TESTOP001 均**未被拒**——接收期科室/操作人暂不做值域硬校验（值域规范率仍会扣质控分）。

### 15.2 crbsb 全集发现与 serial 之谜解封

- **CRBSB schema（ODS）已建全套 20 张规范表视图**（10-15 起建，10-28 上午仍在更新），含与我们完全重叠的 5 类
  （EMR_ADMISSION_RECORD/FIRST_COURSE/DAILY_COURSE/DEATH_INFO/EX_CLINICAL(_ITEM)）+ 16 张 CRBSB_* 字典表。
- **serial_number 全院口径实锤 = `PMI.INP_NO || VISIT_ID`（病案号拼次数，无分隔符）**：crbsb 首页 59.5 万行唯一率
  99.999%；FXHIS 住院 serial 长度 8/9/10 分布（7位病案号+1~3位次数）与近期数据匹配吻合；§13 的"待 DBA 导出"项解除。
- crbsb 其他口径：id=下划线复合（检查表=裸 EXAM_NO）；dept=INFECT_DEPT_DICT 映射（412 行无重复键，覆盖近期出院
  科室 100%，全量 HIS 科室 410/821）；身份证 NVL('-')；医嘱表门诊住院判定=EXAM_MASTER.VISIT_ID='0'。

### 15.3 对 crbsb.emr_order 的审查意见（用户问"是否需要修改"）

| 级别 | 问题 | 修改建议 |
|---|---|---|
| 🔴 | `operation_time` 格式 `yyyymmddhh24miss`，规范要求 `yyyy-MM-dd HH:mm:ss`（我方端点实测规范格式通过，其紧凑格式未验） | 改 `to_char(...,'yyyy-mm-dd hh24:mi:ss')` |
| 🔴 | `serial_number` 用了医嘱级复合键（patient_visit_order_sub），与其自家首页口径（病案号||次数）和规范语义（就诊流水号=住院号）都不一致——若前置机按 serial 关联各表，医嘱会挂不上就诊 | 改 `pmi.INP_NO||a.VISIT_ID`（保持 id 为医嘱级复合键即可） |
| 🟠 | 身份证 `nvl(pmi.id_no,'-')`：证件类别 01 时规范校验 15/18 位，'-' 有拒收/质控风险（住院约 0.15% 无证） | 无证者按规范走"其他法定证件+出生日期8位"分支，或与前置机确认 |
| 🟠 | 科室/药品标量子查询无兜底：INFECT_DEPT_DICT 未映射科室（全量 410/821）产出 NULL 科室码（必填）；drug_dict 若一码多行会 ORA-01427 | 加 NVL 原码兜底；标量子查询改 `(SELECT ... WHERE ... AND ROWNUM=1)` 或改 JOIN |
| 🟡 | `ORDER_CLASS IN ('A','B')`：若 C=中草药则中药处方漏推（规范处方类别含中药） | 核对 ORDER_CLASS 值域后决定是否扩 |
| 🟡 | 视图无时间过滤（全量 orders），推送器需自带水位；operation_time 用 START_DATE_TIME，医嘱停改不触发增量 | 推送器用"水位+重叠回扫+周期对账"组合（与 multi-review F9 共识一致） |
| 🟡 | `prescription_validity_days=TRUNC(stop-start)` 可为空/负数；prescription_dispensing_id 恒空串 | 按规范条件必填规则核对 |

### 15.4 我方 6 张视图 v0.5 对齐（已完成，待 DBA 重建部署）

org=370104000；serial=病案号||次数（01/04=就诊级，02/03=+FILE_UNIQUE_ID，05=+EXAM_NO 维度见文件）；id=下划线
复合（检查=裸 EXAM_NO 对齐 crbsb）；科室=INFECT 映射+原码兜底（比 crbsb 多一层兜底，理由：必填防 NULL）；身份证
NVL('-')。**近期 7 天实测：serial 唯一 1232/1232、出院科室映射 100%、INP_NO 非空 100%。**

### 15.5 两套实现并存→§16 已裁决：crbsb 病程/死亡基于老电子病历，病程必须用我方嘉和源

（原建议"推送器读 CRBSB、V_INF 退役"已被 §16 的事实推翻。）需与 crbsb 作者沟通：病程/死亡三视图换嘉和源
（可参考我方 v0.5 写法）或明确病程由我方供数；emr_order 两条 🔴 修改建议不变。

## 16. crbsb 数据源鉴定：老电子病历实锤（2026-10-28，INF-SOURCE-VERIFY-20261028）

用户质疑"crbsb 是老电子病历"，经依赖分析+新鲜度实证**证实（部分）**：

| crbsb 视图 | 依赖源 | 判定 |
|---|---|---|
| EMR_ADMISSION_RECORD | HIS.PAT_VISIT/PMI/DIAGNOSIS/CDA 字典 | ✅ 现行源 |
| EMR_EX_CLINICAL(_ITEM) | HIS.EXAM_MASTER + PACS/LIS/CS/NJ/XD/BL.PITAYA 多源 | ✅ 现行源 |
| **EMR_FIRST_COURSE** | **MTL.EMRSD37**/EPR_INPATIENT(_DOCINDEX) | ❌ 老电子病历（曼陀罗） |
| **EMR_DAILY_COURSE** | **MTL.EMRSD38**/EPR_INPATIENT(_DOCINDEX) | ❌ 老电子病历 |
| EMR_DEATH_INFO | HIS 驱动 + **MTL.EMRSD36/50** 死亡文书 | ⚠️ 混合（文书部分老库） |

**决定性证据**：MTL.EPR_INPATIENT_DOCINDEX 最后文书 DATE_VALIDATED=**2025-10-22**（切嘉和时间点），
近 7 天新文书 **0 份**——crbsb 病程两视图在切换后**整整一年无数据**；其死亡文书细节同样停更。

**结论（回答用户"是否需要使用新的上传"）**：**是。** 首次/日常病程必须用嘉和（新 EMR）源=我方
V_INF_FIRST_COURSE/DAILY_COURSE（JHEMR，实测每周 1131/1341 篇当前文书）。推荐推送方案：
**统一读我方 V_INF_* v0.5**（6 张全部现行源+口径已对齐 crbsb，单源最简）；或按表混用（首页/检查可读
crbsb，病程必读 V_INF）——无论哪种，**病程不能走 crbsb，且严禁双推**。

## 17. 上报启动清单（2026-10-28 用户拍板：统一走 V_INF_* v0.5 新源）

| # | 事项 | 责任方 | 状态/说明 |
|---|---|---|---|
| 1 | **重建部署 6 张 v0.5 视图** | DBA/用户 | **10-28 10:17 全部重建完成，6/6 验收通过**（§18：org/serial/字段全绿，检查主表 5424 例/周 serial 就诊级粒度正确） |
| 2 | 部署后复核（6 张 VALID+近 7 天行数+serial 唯一率+科室映射率） | 我 | 视图建完即跑 |
| 3 | **推送器专用只读账号**（最小权限：仅 SELECT 6 张 V_INF_*，不用属主/DBA 账号；凭据入 worker 环境） | DBA/用户 | 治理共识（multi-review F13） |
| 4 | **推送器**：已按独立工具交付 `implementation/deploy/inf_pusher/`（pusher.py+tables.py+config 模板+README），26/26 自测通过（2026-10-28 INF-PUSHER-BUILD）。含：MOCK 默认/real 双门槛、水位+3天重叠回扫+full_days 周期对账、次序保护（sent.jsonl）、必填客户端预检、回执 JSONL、01/99 重试·02/03/04 不重试、白名单硬编码、截断统计、主子表排序。注：为最快上线采用独立脚本（DMZ forwarder 同模式）而非域插件——功能等价、部署面小；二期如需并入 blyb 栈再迁移 | 我 | **代码完成**，待部署主机安装运行 |
| 5 | MOCK dry-run 全链路（零真实副作用） | 我 | 依赖 4 |
| 6 | 真实小批量试推（建议首推死亡表：量小每天个位数，含 DELETE 校验） | 我+用户 | 依赖 1-5；用户点头后推真实数据 |
| 7 | 按表放量：死亡→首页→检查→病程（逐表开、每表看回执再开下一张） | 我 | 依赖 6 |
| 8 | 对账与质控自评（前置机回执 vs 视图行数；必填完整率/及时率） | 我 | 持续 |
| 9 | 与 crbsb 作者确认：5 类文书由我方唯一推送（**严禁双推**）；其病程视图老源问题与 emr_order 两条🔴转达 | 用户/信息科 | 协调项 |
| 10 | 伴生表归属确认：patient_info/activity/base_user/base_dept 谁推（FXHIS/crbsb 侧有无推送计划）——不推不阻断接收但扣关联质控分 | 用户/信息科 | 协调项 |

**联调时盯的已知尾差（不阻断）**：身份证缺失 4 例/周（'-' 兜底是否被拒，实测定）；门急诊诊断 3 例/周无码；嘉和补改是否换 FILE_UNIQUE_ID（换则前置机视为新增，需对账发现）；license 仅单点登录用（本期不需要）。

**关键路径**：1→2→(4 并行)→5→6→7。用户完成 1/3/9 后即可进入真实推送。

## 18. v0.5 部署验收（2026-10-28 09:59 重建，INF-V05-ACCEPT-20261028）

5/6 通过：首页（org 新✓/serial 唯一 1246/1246）、死亡（21例/30天，serial/死亡时间/死因 100%）、首次病程 1000/周、
日常病程 1201/周（serial 零空）、检查项目子表 VALID。**V_INF_EX_CLINICAL 已于 10:17 重建并验收通过**（5424 例/周，serial 100% 非空且就诊级粒度正确 1653 唯一、客观所见 98.5%、报告时间 100%、org=370104000；项目子表 5374 行编码/外键 100%）。
**上传启动判定（终）**：视图层 **6/6 完工**；剩余=推送器程序（§17 #4，我方）+专用只读账号（真实推送前配上即可）。

## 19. 推送器交付（2026-10-28，INF-PUSHER-BUILD-20261028）

`implementation/deploy/inf_pusher/`：**26/26 自测通过**（camel 转换/日期格式/必填预检/次序保护/水位与重叠窗/
白名单放行+4 类拦截/注册表一致性/mock 端到端/real 守门/截断统计）。**已部署 <prod-host>（2026-10-28 INF-PUSHER-DEPLOY）**：经 <jump-host>:<ssh-port> 跳板上传（base64 分块+sha256 全对），
双可达实测 OPEN（ODS 8.216:1521 / 前置机 <frontend-host>）；运行位置=<worker-container> 容器（oracledb 3.4.2+
instantclient，thick 自动初始化已内置）；文件在 <deploy-path>（宿主）与 <prod-path>/exchange/inf-pusher（容器
/data/exchange/inf-pusher，持久挂载）；容器内自测 **26/26 通过**；config.json（enabled=false）与 run.sh（750，
自动加载 inf.env 凭据）就位。
**唯一剩余（INF-FIRST-RUN 负向验证完成）**：执行管道已验证到"精确缺凭据为止"（容器用户 10001 权限已修正，
run.sh→pusher→Oracle 层全通，报错信息友好化）。DBA 按 `implementation/deploy/inf_pusher/dba-setup.sql`
建 INF_PUSHER 只读账号并写入 `<prod-path>/exchange/inf-pusher/inf.env`（600，10001）后，按序执行：
`docker exec <worker-container> /data/exchange/inf-pusher/run.sh all mock`（真实数据演练）
→ `... run.sh death real 3`（首推 3 条，需把 config.json 的 enabled 改 true）
→ `... run.sh death real` → 逐表放量 → 成功后宿主 cron `30 8 * * *`。

## 20. 首次全表 MOCK 实跑（2026-10-28，INF-GO，用户授权用平台 ODS 账号）

- **凭据注入完成**：平台 ods_internal 账号经"<relay-jump> 容器取→跳板 stdin 注入（stty -echo+行首 ^D 配方，探针验证）"
  写入 <prod-host> inf.env（600/10001），全程零回显；run.sh 修复 export。
- **全表 MOCK（真实读数零发送）**：death 199/199 全过；admission 30593 行 97% 过（785 缺必填：operator/门急诊诊断尾差）；
  first_course 5335/5350（truncated 820 触顶标记）；daily_course 5109/5136；
  **exam 41545 与 exam_item 41975 全部拦截**——EXAM_SUBDEPT 大量为空致 deptCode/deptName 空、CONFIRM_DOCT 空致
  operatorId 空。
- **发现推送器真 bug**：次序保护键未分表（death/admission 共用 patient_visit 复合 ID 串掉 199 条）——已修复为
  table/id 键（自测 26/26 保持）。
- **三张视图兜底修正（活库预验证 100% 填补，待 DBA 重跑）**：01 操作人 NVL(主治,住院医师) 30663/30663；
  05 报告科室 EXAM_SUBDEPT 空→兜底申请科室（映射后）5125/5125；06 操作人 COALESCE(确认医师,报告医师,申请医师)
  5125/5125。**DBA 重跑 01/05/06 后清状态重跑全表 MOCK 应全绿**（状态已清）。

## 21. 真实上线与 T+0 定时推送（2026-10-28，INF-TIMEDPUSH）

**真实首推与全量回填（前置机 <frontend-host>，docker exec -d 分离模式）**：
- 逐表首推 3 条全部受理（death 初次 3×errorCode05 → 根因=CDA 死亡表 DEATH_DATE 为 VARCHAR2，视图 NVL 把 DATE
  强转 Oracle 默认 'DD-MON-26' 串；推送器加 locale 无关日期串归一化后 3/3 通过）；
- **全量回填完成：128,625 条受理（~200 条/秒，约 10 分钟）**，未决失败 0；尾差：exam_item 孤儿 89、
  首页 operator+诊断全无 11（已知数据尾差）；病程 2000 字触顶 819 条已标记。
- 垫片机制上线：exam 科室→申请科室、exam_item 操作人→同批主表、admission 操作人→住院医师、日期串归一化
  （视图侧兜片重跑后自动失效）；MOCK 不再推水位/记已发（修复污染真实推送的状态缺陷）；次序保护键分表。
- **定时任务已装（root crontab@<prod-host>）**：`30 8,11,14,17,20 * * *` + `55 23 * * *`，flock 防重叠，
  run.sh all real，日志 state/cron.log——**满足 T+0（当日 24 点前全推）且避开 drug_trace 06:00-08:30 维护窗**，
  首班 8:30 前夜间数据当日补齐。
- 视图侧遗留（下次 DBA 重跑 01/04/05/06 时消化，垫片已兜住不影响运行）：01 操作人 NVL、04 死亡时间 TO_DATE 根治、
  05 科室兜底、06 操作人 COALESCE。
- 伴生表归属、与 crbsb 作者的双推边界确认，仍为协调项。
