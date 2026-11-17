-- =============================================================================
-- 传染病前置软件上报 · 死亡信息（emr_death_info）取数视图 v0.6 —— 死亡 T+0 修复
-- 任务：INF-DEATH-FIX-20261105 ｜ 计划：数据上报/4、传染病病历数据上报/plans/20261105-死亡T0修复执行计划.md
-- 库：ODS ｜ 目标接口 /hclient/emr/receive/emrDeathInfo
--
-- 【v0.6 变更（相对在库 v0.5，2026-11-05）】
--   驱动切换：死亡行 = 嘉和死亡记录文书（T+0 主驱动）
--              ∪ PAT_VISIT 离院方式=5（v0.5 原逻辑，兜历史与无文书病例）
--            按 ID=PATIENT_ID||'_'||VISIT_ID 去重：同一就诊文书行优先，
--            PAT_VISIT 行仅在该就诊无死亡文书时保留（NOT EXISTS 排除）。
--   背景：病案编码补填 DISCHARGE_DISPOSITION='5' 滞后数天（T+0 不可达）；
--         CDA.TT_ESBHL_DEATH_ROD 2024-08-30 起停更（E5）。
--   列名/顺序与 v0.5 完全一致（推送器 tables.py 按列名取值，列名变=断链）。
--
-- 【字段决策（Ph1 实测定稿，2026-11-05）】
--   文书筛选：TOPIC IN ('死亡记录','24小时内入院死亡记录') OR TOPIC LIKE '死亡记录模板%'
--     —— 明确排除"死亡病例讨论结论记录"（死后数天补写，做驱动反而漏 T+0）。
--     注：LIKE '死亡记录模板%' 不会误伤 '死亡病例讨论%'（前缀第 3 字即分叉：记 vs 病）。
--   每就诊多份文书：ROW_NUMBER 按 CREATE_DATE_TIME 取首份（首份死亡记录即事件时刻），
--     ROWID 作并列打破，保证确定性。
--   OPERATION_TIME：文书行=该份文书 LAST_MODIFY_DATE_TIME（文书修订推新 op → 幂等更新重推，
--     前置机按 ID 更新，设计内）；PAT_VISIT 行=DISCHARGE_DATE_TIME（同 v0.5）。
--   DEAD_DATE：CDA（停更实际恒空，链位保留）→ PAT_VISIT.DISCHARGE_DATE_TIME
--     → 兜底文书 CREATE_DATE_TIME（Ph1-2：近 30 天 37/37 有离院时间，兜底仅覆盖瞬时窗口）。
--     注：DEATH_DATE 为 VARCHAR 停更字段，不做 TO_DATE 强转（E5 坑）；NVL(VARCHAR, DATE)
--     的隐式转换行为与在库 v0.5 完全一致，不新增转换。
--   DIRECT_CAUSE_*：沿用 v0.5 链（CDA → 出院主诊断经 CDA_DICTIONARY 院标→国标映射）。
--   DEATH_DIAGNOSIS_*（质控点名"死亡诊断代码缺失"）：CDA → 新增兜底=出院主诊断映射
--     （与 DIRECT_CAUSE 同一 fallback 查询；CDA 复活后自然优先）。此为 v0.5→v0.6 唯一口径增强。
--   DEPT_CODE/NAME：文书行首选 PATIENT_CUR_DEPT_CODE（文书时在院科室，Ph1-2 非空 100%）
--     → INFECT_DEPT_DICT 映射，兜底 v0.5 的出院科室链。
--   OPERATOR_ID / CHIEF_PHYSICIAN_ID：文书行=CREATOR_ID（写死亡记录的医生），兜底主治医师。
--   其余身份/机构/serial 字段沿用 v0.5（PAT_MASTER_INDEX 关联、INP_NO||VISIT_ID、'-' 兜底）。
--
-- 【量级预期（Ph1-4 预演，DBA 执行后以此比对）】
--   全表 ≈342 行（=v0.5 的 329 + 文书独有 13）；11 月（op>=2026-11-01）≥6。
--   死亡年量级 ~400，无需 ROWNUM 限量。
--
-- 【执行方式】本文件由 DBA 在 ODS 库执行；AI 不执行任何 DDL（红线 1）。
-- 【部署记录】DBA 已于 2026-11-06 执行；复核 5 条验证全过（342 行/11 月 6 行/必填 11 月全 0 空），
--   补传 sent=323 failed=0、唯一 ID 341/342 全覆盖——证据：数据上报/4、传染病病历数据上报/
--   01-项目过程与决策记录.md 与 docs/ai/sessions/INF-DEATH-FIX2-20261106.md。
-- =============================================================================

CREATE OR REPLACE FORCE VIEW "ODS"."V_INF_DEATH_INFO" (
  "ID", "PATIENT_ID", "ACTIVITY_TYPE_CODE", "ACTIVITY_TYPE_NAME",
  "SERIAL_NUMBER", "PATIENT_NAME",
  "ID_CARD_TYPE_CODE", "ID_CARD_TYPE_NAME", "ID_CARD",
  "TREATMENT_DESC", "DEAD_DATE",
  "DIRECT_CAUSE_CODE", "DIRECT_CAUSE_NAME",
  "DEATH_DIAGNOSIS_CODE", "DEATH_DIAGNOSIS_NAME",
  "CHIEF_PHYSICIAN_ID",
  "ORG_CODE", "ORG_NAME", "DEPT_CODE", "DEPT_NAME",
  "OPERATOR_ID", "OPERATION_TIME"
) AS
WITH dw AS (  -- 每就诊首份死亡记录文书（T+0 主驱动）
  SELECT w.PATIENT_ID, w.VISIT_ID, w.CREATOR_ID, w.PATIENT_CUR_DEPT_CODE,
         w.CREATE_DATE_TIME, w.LAST_MODIFY_DATE_TIME
  FROM (
    SELECT d.PATIENT_ID, d.VISIT_ID, d.CREATOR_ID, d.PATIENT_CUR_DEPT_CODE,
           d.CREATE_DATE_TIME, d.LAST_MODIFY_DATE_TIME,
           ROW_NUMBER() OVER (PARTITION BY d.PATIENT_ID, TO_CHAR(d.VISIT_ID)
                              ORDER BY d.CREATE_DATE_TIME, ROWID) AS rn
      FROM JHEMR.JHMR_FILE_INDEX d
     WHERE (d.TOPIC IN ('死亡记录','24小时内入院死亡记录') OR d.TOPIC LIKE '死亡记录模板%')
       AND d.CREATE_DATE_TIME >= TO_DATE('2026-01-01', 'YYYY-MM-DD')
  ) w
  WHERE w.rn = 1
)
-- ---------- 分支 1：死亡文书行（T+0 主驱动；PAT_VISIT 仅取补充字段，LEFT JOIN 不丢行） ----------
SELECT
  TO_CHAR(w.PATIENT_ID) || '_' || TO_CHAR(w.VISIT_ID) AS ID,

  TO_CHAR(w.PATIENT_ID) AS PATIENT_ID,
  '6' AS ACTIVITY_TYPE_CODE,                                     -- 诊疗活动类型代码表：6=住院
  '住院' AS ACTIVITY_TYPE_NAME,
  pmi.INP_NO || TO_CHAR(w.VISIT_ID) AS SERIAL_NUMBER,            -- crbsb 口径=病案号||住院次数
  SUBSTRB(TO_CHAR(pmi.NAME), 1, 100) AS PATIENT_NAME,

  '01' AS ID_CARD_TYPE_CODE,
  '居民身份证' AS ID_CARD_TYPE_NAME,
  NVL(TO_CHAR(pmi.ID_NO), '-') AS ID_CARD,                       -- crbsb 同款兜底

  (SELECT SUBSTRB(MAX(d.TREAT_PROC_DESCR), 1, 2000)               -- 诊疗过程描述（建议填）
     FROM CDA.TT_ESBHL_DEATH_ROD d
    WHERE d.PAT_ID_NUMBER = pmi.ID_NO
      AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)) AS TREATMENT_DESC,

  -- 死亡时间（必填）：CDA（停更恒空，链位保留）→ 离院时间 → 兜底文书创建时间
  NVL((SELECT MAX(d.DEATH_DATE)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)),
      NVL(a.DISCHARGE_DATE_TIME, w.CREATE_DATE_TIME)) AS DEAD_DATE,

  -- 直接死因（必填）：同 v0.5 链——CDA 优先，兜底=出院主诊断经院标→国标 ICD10 映射
  NVL((SELECT SUBSTRB(MAX(d.DIRECT_DEATH_REASON_CODE), 1, 50)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标编码))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_CODE)), 1, 50)
         FROM HIS.DIAGNOSIS dg
        WHERE TO_CHAR(dg.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)
          AND TO_CHAR(dg.VISIT_ID) = TO_CHAR(w.VISIT_ID)
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DIRECT_CAUSE_CODE,
  NVL((SELECT SUBSTRB(MAX(d.DIRECT_DEATH_REASON_NAME), 1, 250)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标名称))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_DESC)), 1, 250)
         FROM HIS.DIAGNOSIS dg
        WHERE TO_CHAR(dg.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)
          AND TO_CHAR(dg.VISIT_ID) = TO_CHAR(w.VISIT_ID)
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DIRECT_CAUSE_NAME,

  -- 死亡诊断（传染病死亡时必填；质控点名缺失项）：CDA 优先，新增兜底=出院主诊断映射（v0.6 变更）
  NVL((SELECT SUBSTRB(MAX(d.DEATH_DIAG_CODE), 1, 50)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标编码))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_CODE)), 1, 50)
         FROM HIS.DIAGNOSIS dg
        WHERE TO_CHAR(dg.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)
          AND TO_CHAR(dg.VISIT_ID) = TO_CHAR(w.VISIT_ID)
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DEATH_DIAGNOSIS_CODE,
  NVL((SELECT SUBSTRB(MAX(d.DEATH_DIAG_NAME), 1, 250)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标名称))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_DESC)), 1, 250)
         FROM HIS.DIAGNOSIS dg
        WHERE TO_CHAR(dg.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)
          AND TO_CHAR(dg.VISIT_ID) = TO_CHAR(w.VISIT_ID)
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DEATH_DIAGNOSIS_NAME,

  NVL(TO_CHAR(w.CREATOR_ID), TO_CHAR(a.ATTENDING_DOCTOR)) AS CHIEF_PHYSICIAN_ID,  -- 写死亡记录医生，兜底主治

  '370104000' AS ORG_CODE,
  '山东省第二人民医院' AS ORG_NAME,
  -- 死亡科室：文书时在院科室（Ph1-2 非空 100%）INFECT 映射 → 出院科室 INFECT 映射 → 原码兜底
  NVL((SELECT MAX(dd.INFECT_DEPT_CODE) FROM HIS.INFECT_DEPT_DICT dd
        WHERE TO_CHAR(dd.HIS_DEPT_CODE) = TO_CHAR(w.PATIENT_CUR_DEPT_CODE)),
      NVL((SELECT MAX(dd2.INFECT_DEPT_CODE) FROM HIS.INFECT_DEPT_DICT dd2
            WHERE TO_CHAR(dd2.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)),
          TO_CHAR(w.PATIENT_CUR_DEPT_CODE))) AS DEPT_CODE,
  NVL((SELECT MAX(x.INFECT_DEPT_NAME) FROM HIS.INFECT_DEPT_DICT x
        WHERE TO_CHAR(x.HIS_DEPT_CODE) = TO_CHAR(w.PATIENT_CUR_DEPT_CODE)),
      NVL((SELECT MAX(x2.INFECT_DEPT_NAME) FROM HIS.INFECT_DEPT_DICT x2
            WHERE TO_CHAR(x2.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)),
          NVL((SELECT MAX(dd.DEPT_NAME) FROM HIS.DEPT_DICT dd
                WHERE TO_CHAR(dd.DEPT_CODE) = TO_CHAR(w.PATIENT_CUR_DEPT_CODE)),
              (SELECT MAX(dd2.DEPT_NAME) FROM HIS.DEPT_DICT dd2
                WHERE TO_CHAR(dd2.DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM))))) AS DEPT_NAME,

  NVL(TO_CHAR(w.CREATOR_ID), TO_CHAR(a.ATTENDING_DOCTOR)) AS OPERATOR_ID,         -- 写死亡记录医生，兜底主治
  w.LAST_MODIFY_DATE_TIME AS OPERATION_TIME                      -- 增量水位：文书修订即推新
FROM dw w
LEFT JOIN HIS.PAT_VISIT a
       ON TO_CHAR(a.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)
      AND TO_CHAR(a.VISIT_ID)   = TO_CHAR(w.VISIT_ID)
LEFT JOIN HIS.PAT_MASTER_INDEX pmi ON TO_CHAR(pmi.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)

UNION ALL
-- ---------- 分支 2：PAT_VISIT 离院方式=5（v0.5 原逻辑，兜历史与无文书病例） ----------
SELECT
  a.PATIENT_ID || '_' || a.VISIT_ID AS ID,

  a.PATIENT_ID AS PATIENT_ID,
  '6' AS ACTIVITY_TYPE_CODE,                                     -- 诊疗活动类型代码表：6=住院
  '住院' AS ACTIVITY_TYPE_NAME,
  pmi.INP_NO || a.VISIT_ID AS SERIAL_NUMBER,  -- crbsb 口径=病案号||住院次数
  SUBSTRB(TO_CHAR(pmi.NAME), 1, 100) AS PATIENT_NAME,

  '01' AS ID_CARD_TYPE_CODE,
  '居民身份证' AS ID_CARD_TYPE_NAME,
  NVL(TO_CHAR(pmi.ID_NO), '-') AS ID_CARD,  -- crbsb 同款兜底

  (SELECT SUBSTRB(MAX(d.TREAT_PROC_DESCR), 1, 2000)               -- 诊疗过程描述（建议填）
     FROM CDA.TT_ESBHL_DEATH_ROD d
    WHERE d.PAT_ID_NUMBER = pmi.ID_NO
      AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)) AS TREATMENT_DESC,

  -- 死亡时间（必填）：CDA 死亡记录优先，兜底出院时间（三甲验证口径）
  NVL((SELECT MAX(d.DEATH_DATE)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
      a.DISCHARGE_DATE_TIME) AS DEAD_DATE,

  -- 直接死因（必填，院内ICD10死因编码）：CDA 直接死因优先，
  -- 兜底=出院主诊断经院标→国标 ICD10 映射（三甲死亡视图同模式）
  NVL((SELECT SUBSTRB(MAX(d.DIRECT_DEATH_REASON_CODE), 1, 50)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标编码))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_CODE)), 1, 50)
         FROM HIS.DIAGNOSIS dg
        WHERE dg.PATIENT_ID = a.PATIENT_ID AND dg.VISIT_ID = a.VISIT_ID
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DIRECT_CAUSE_CODE,
  NVL((SELECT SUBSTRB(MAX(d.DIRECT_DEATH_REASON_NAME), 1, 250)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标名称))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_DESC)), 1, 250)
         FROM HIS.DIAGNOSIS dg
        WHERE dg.PATIENT_ID = a.PATIENT_ID AND dg.VISIT_ID = a.VISIT_ID
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DIRECT_CAUSE_NAME,

  -- 死亡诊断（传染病死亡时必填）：CDA 优先，新增兜底=出院主诊断映射（v0.6 变更，质控点名项）
  NVL((SELECT SUBSTRB(MAX(d.DEATH_DIAG_CODE), 1, 50)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标编码))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_CODE)), 1, 50)
         FROM HIS.DIAGNOSIS dg
        WHERE dg.PATIENT_ID = a.PATIENT_ID AND dg.VISIT_ID = a.VISIT_ID
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DEATH_DIAGNOSIS_CODE,
  NVL((SELECT SUBSTRB(MAX(d.DEATH_DIAG_NAME), 1, 250)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标名称))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_DESC)), 1, 250)
         FROM HIS.DIAGNOSIS dg
        WHERE dg.PATIENT_ID = a.PATIENT_ID AND dg.VISIT_ID = a.VISIT_ID
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DEATH_DIAGNOSIS_NAME,

  TO_CHAR(a.ATTENDING_DOCTOR) AS CHIEF_PHYSICIAN_ID,             -- 主治医师（平台核对列名）

  '370104000' AS ORG_CODE,
  '山东省第二人民医院' AS ORG_NAME,
  NVL((SELECT MAX(dd.INFECT_DEPT_CODE) FROM HIS.INFECT_DEPT_DICT dd WHERE TO_CHAR(dd.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)), TO_CHAR(a.DEPT_DISCHARGE_FROM)) AS DEPT_CODE,  -- 死亡科室=出院科室，INFECT 映射+兜底
  NVL((SELECT MAX(x.INFECT_DEPT_NAME) FROM HIS.INFECT_DEPT_DICT x
      WHERE TO_CHAR(x.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)),
      (SELECT MAX(dd.DEPT_NAME) FROM HIS.DEPT_DICT dd
      WHERE TO_CHAR(dd.DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM))) AS DEPT_NAME,

  TO_CHAR(a.ATTENDING_DOCTOR) AS OPERATOR_ID,                    -- PAT_VISIT 无 OPERATOR 列
  a.DISCHARGE_DATE_TIME AS OPERATION_TIME                        -- 增量水位（同 v0.5）
FROM HIS.PAT_VISIT a
JOIN HIS.PAT_MASTER_INDEX pmi ON pmi.PATIENT_ID = a.PATIENT_ID
WHERE a.DISCHARGE_DISPOSITION = '5'                              -- 离院方式5=死亡（148号口径）；列已核对存在
  AND a.DISCHARGE_DATE_TIME >= TO_DATE('2026-01-01', 'YYYY-MM-DD')
  AND NOT EXISTS (                                               -- 去重：该就诊已有死亡文书行则不重复输出
        SELECT 1 FROM dw w2
         WHERE TO_CHAR(w2.PATIENT_ID) = TO_CHAR(a.PATIENT_ID)
           AND TO_CHAR(w2.VISIT_ID)   = TO_CHAR(a.VISIT_ID))
WITH READ ONLY;

-- 授权说明：CREATE OR REPLACE 不影响既有授权（推送器现有 ODS 账号权限保留）。
-- 仅当需要为新账号授权时执行（账号名由 DBA 侧确认，勿读 inf.env）：
-- GRANT SELECT ON ODS.V_INF_DEATH_INFO TO <推送器ODS账号>;

-- =============================================================================
-- 【DBA 执行后 5 条验证 SQL】（结果回传执行 AI 复核；任何一条异常 → 执行文末回滚段）
-- 1. 总行数（预期 ≈342 = v0.5 的 329 + 文书独有 13，随当日新增文书浮动）：
-- SELECT COUNT(*) FROM ODS.V_INF_DEATH_INFO;
--
-- 2. 按月分布（预期：PAT_VISIT 行按离院月 + 文书独有行按文书修订月，总量对齐 Ph1 预演）：
-- SELECT TO_CHAR(OPERATION_TIME,'YYYY-MM') YM, COUNT(*) FROM ODS.V_INF_DEATH_INFO GROUP BY TO_CHAR(OPERATION_TIME,'YYYY-MM') ORDER BY YM;
--
-- 3. MAX(OPERATION_TIME)（预期 ≥ 2026-11-04：11 月文书行已入驱动）：
-- SELECT MAX(OPERATION_TIME) FROM ODS.V_INF_DEATH_INFO;
--
-- 4. 必填空值率（预期 11 月行全 0；全表 DEAD_DATE/DIRECT_CAUSE_CODE 空数应较 v0.5 显著下降）：
-- SELECT COUNT(*) TOTAL,
--        SUM(CASE WHEN DEAD_DATE IS NULL THEN 1 ELSE 0 END) DEAD_NULL,
--        SUM(CASE WHEN DIRECT_CAUSE_CODE IS NULL THEN 1 ELSE 0 END) DIRECT_NULL,
--        SUM(CASE WHEN DEATH_DIAGNOSIS_CODE IS NULL THEN 1 ELSE 0 END) DEATHDIAG_NULL,
--        SUM(CASE WHEN DEPT_CODE IS NULL THEN 1 ELSE 0 END) DEPT_NULL,
--        SUM(CASE WHEN OPERATOR_ID IS NULL THEN 1 ELSE 0 END) OPERATOR_NULL,
--        SUM(CASE WHEN OPERATION_TIME >= DATE'2026-11-01' THEN 1 ELSE 0 END) NOV_ROWS
--   FROM ODS.V_INF_DEATH_INFO;
--
-- 5. 11 月行数（预期 ≥6；部署时刻随当日新增 +1~2）：
-- SELECT COUNT(*) FROM ODS.V_INF_DEATH_INFO WHERE OPERATION_TIME >= DATE'2026-11-01';
-- =============================================================================

-- =============================================================================
-- 【回滚段 = v0.5 全文（当前在库版本）】回滚方式二选一：
--   ① 去掉本段每行行首 "--" 后执行；② 直接执行基线文件 sql/04-v_inf_death_info.sql。
-- 回滚后推送器自动按旧口径续跑（恢复"滞后但可跑"状态）。
-- =============================================================================
-- CREATE OR REPLACE FORCE VIEW "ODS"."V_INF_DEATH_INFO" (
--   "ID", "PATIENT_ID", "ACTIVITY_TYPE_CODE", "ACTIVITY_TYPE_NAME",
--   "SERIAL_NUMBER", "PATIENT_NAME",
--   "ID_CARD_TYPE_CODE", "ID_CARD_TYPE_NAME", "ID_CARD",
--   "TREATMENT_DESC", "DEAD_DATE",
--   "DIRECT_CAUSE_CODE", "DIRECT_CAUSE_NAME",
--   "DEATH_DIAGNOSIS_CODE", "DEATH_DIAGNOSIS_NAME",
--   "CHIEF_PHYSICIAN_ID",
--   "ORG_CODE", "ORG_NAME", "DEPT_CODE", "DEPT_NAME",
--   "OPERATOR_ID", "OPERATION_TIME"
-- ) AS
-- SELECT
--   a.PATIENT_ID || '_' || a.VISIT_ID AS ID,
--
--   a.PATIENT_ID AS PATIENT_ID,
--   '6' AS ACTIVITY_TYPE_CODE,
--   '住院' AS ACTIVITY_TYPE_NAME,
--   pmi.INP_NO || a.VISIT_ID AS SERIAL_NUMBER,
--   SUBSTRB(TO_CHAR(pmi.NAME), 1, 100) AS PATIENT_NAME,
--
--   '01' AS ID_CARD_TYPE_CODE,
--   '居民身份证' AS ID_CARD_TYPE_NAME,
--   NVL(TO_CHAR(pmi.ID_NO), '-') AS ID_CARD,
--
--   (SELECT SUBSTRB(MAX(d.TREAT_PROC_DESCR), 1, 2000)
--      FROM CDA.TT_ESBHL_DEATH_ROD d
--     WHERE d.PAT_ID_NUMBER = pmi.ID_NO
--       AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)) AS TREATMENT_DESC,
--
--   NVL((SELECT MAX(d.DEATH_DATE)
--          FROM CDA.TT_ESBHL_DEATH_ROD d
--         WHERE d.PAT_ID_NUMBER = pmi.ID_NO
--           AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
--       a.DISCHARGE_DATE_TIME) AS DEAD_DATE,
--
--   NVL((SELECT SUBSTRB(MAX(d.DIRECT_DEATH_REASON_CODE), 1, 50)
--          FROM CDA.TT_ESBHL_DEATH_ROD d
--         WHERE d.PAT_ID_NUMBER = pmi.ID_NO
--           AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
--       (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标编码))
--                                  FROM CDA.CDA_DICTIONARY cd
--                                 WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
--                                   AND cd.院标编码 = dg.DIAGNOSIS_CODE),
--                               dg.DIAGNOSIS_CODE)), 1, 50)
--          FROM HIS.DIAGNOSIS dg
--         WHERE dg.PATIENT_ID = a.PATIENT_ID AND dg.VISIT_ID = a.VISIT_ID
--           AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DIRECT_CAUSE_CODE,
--   NVL((SELECT SUBSTRB(MAX(d.DIRECT_DEATH_REASON_NAME), 1, 250)
--          FROM CDA.TT_ESBHL_DEATH_ROD d
--         WHERE d.PAT_ID_NUMBER = pmi.ID_NO
--           AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
--       (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标名称))
--                                  FROM CDA.CDA_DICTIONARY cd
--                                 WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
--                                   AND cd.院标编码 = dg.DIAGNOSIS_CODE),
--                               dg.DIAGNOSIS_DESC)), 1, 250)
--          FROM HIS.DIAGNOSIS dg
--         WHERE dg.PATIENT_ID = a.PATIENT_ID AND dg.VISIT_ID = a.VISIT_ID
--           AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DIRECT_CAUSE_NAME,
--
--   (SELECT SUBSTRB(MAX(d.DEATH_DIAG_CODE), 1, 50)
--      FROM CDA.TT_ESBHL_DEATH_ROD d
--     WHERE d.PAT_ID_NUMBER = pmi.ID_NO
--       AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)) AS DEATH_DIAGNOSIS_CODE,
--   (SELECT SUBSTRB(MAX(d.DEATH_DIAG_NAME), 1, 250)
--      FROM CDA.TT_ESBHL_DEATH_ROD d
--     WHERE d.PAT_ID_NUMBER = pmi.ID_NO
--       AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)) AS DEATH_DIAGNOSIS_NAME,
--
--   TO_CHAR(a.ATTENDING_DOCTOR) AS CHIEF_PHYSICIAN_ID,
--
--   '370104000' AS ORG_CODE,
--   '山东省第二人民医院' AS ORG_NAME,
--   NVL((SELECT MAX(dd.INFECT_DEPT_CODE) FROM HIS.INFECT_DEPT_DICT dd WHERE TO_CHAR(dd.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)), TO_CHAR(a.DEPT_DISCHARGE_FROM)) AS DEPT_CODE,
--   NVL((SELECT MAX(x.INFECT_DEPT_NAME) FROM HIS.INFECT_DEPT_DICT x
--       WHERE TO_CHAR(x.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)),
--       (SELECT MAX(dd.DEPT_NAME) FROM HIS.DEPT_DICT dd
--       WHERE TO_CHAR(dd.DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM))) AS DEPT_NAME,
--
--   TO_CHAR(a.ATTENDING_DOCTOR) AS OPERATOR_ID,
--   a.DISCHARGE_DATE_TIME AS OPERATION_TIME
-- FROM HIS.PAT_VISIT a
-- JOIN HIS.PAT_MASTER_INDEX pmi ON pmi.PATIENT_ID = a.PATIENT_ID
-- WHERE a.DISCHARGE_DISPOSITION = '5'
--   AND a.DISCHARGE_DATE_TIME >= TO_DATE('2026-01-01', 'YYYY-MM-DD')
-- WITH READ ONLY;
-- =============================================================================
